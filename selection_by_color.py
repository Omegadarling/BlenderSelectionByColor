bl_info = {
    "name": "Selection by Color",
    "author": "",
    "version": (1, 0, 0),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar > Color Select",
    "description": "Create vertex group selection sets by clustering material colors",
    "category": "Mesh",
}

import bpy
import numpy as np
from bpy.props import IntProperty, EnumProperty, PointerProperty
from bpy.types import PropertyGroup, Operator, Panel


# ────────────────────────────────────────────────────────────────
# K-Means clustering (no external dependencies)
# ────────────────────────────────────────────────────────────────

def _kmeans(data, k, max_iters=30, seed=42):
    """K-means with k-means++ init. Returns (labels, centroids)."""
    rng = np.random.default_rng(seed)
    n, d = data.shape
    centroids = np.empty((k, d), dtype=np.float32)

    # K-means++ initialisation
    centroids[0] = data[rng.integers(n)]
    for i in range(1, k):
        min_d2 = np.full(n, np.inf, dtype=np.float32)
        for j in range(i):
            d2 = np.sum((data - centroids[j]) ** 2, axis=1)
            np.minimum(min_d2, d2, out=min_d2)
        probs = min_d2 / min_d2.sum()
        centroids[i] = data[rng.choice(n, p=probs)]

    # Iterative refinement
    labels = np.zeros(n, dtype=np.int32)
    for _ in range(max_iters):
        # Assign
        min_d2 = np.full(n, np.inf, dtype=np.float32)
        for j in range(k):
            d2 = np.sum((data - centroids[j]) ** 2, axis=1)
            closer = d2 < min_d2
            labels[closer] = j
            np.minimum(min_d2, d2, out=min_d2)
        # Update
        new_c = np.empty_like(centroids)
        for j in range(k):
            members = data[labels == j]
            new_c[j] = members.mean(axis=0) if len(members) else centroids[j]
        if np.allclose(centroids, new_c, atol=1e-6):
            break
        centroids = new_c

    return labels, centroids


# ────────────────────────────────────────────────────────────────
# Face ↔ loop mapping
# ────────────────────────────────────────────────────────────────

def _face_loop_map(mesh):
    """Return (face_index_per_loop, loop_total_per_face) arrays."""
    n_faces = len(mesh.polygons)
    loop_totals = np.empty(n_faces, dtype=np.int32)
    mesh.polygons.foreach_get('loop_total', loop_totals)
    face_indices = np.repeat(np.arange(n_faces, dtype=np.int32), loop_totals)
    return face_indices, loop_totals


# ────────────────────────────────────────────────────────────────
# Color extraction
# ────────────────────────────────────────────────────────────────

def _colors_from_vertex_attr(mesh):
    """Per-face average color from the active vertex-color attribute."""
    if not mesh.color_attributes:
        return None
    attr = mesh.color_attributes.active_color or mesh.color_attributes[0]
    n_loops = len(mesh.loops)
    n_faces = len(mesh.polygons)

    raw = np.empty(n_loops * 4, dtype=np.float32)
    attr.data.foreach_get('color', raw)
    loop_rgb = raw.reshape(n_loops, 4)[:, :3]

    fi, lt = _face_loop_map(mesh)
    face_sum = np.zeros((n_faces, 3), dtype=np.float64)
    np.add.at(face_sum, fi, loop_rgb)
    return (face_sum / lt[:, None]).astype(np.float32)


def _find_base_color_image(mat):
    """Return (Image, uv_map_name|None) from the Principled BSDF."""
    if not mat or not mat.use_nodes:
        return None, None
    for node in mat.node_tree.nodes:
        if node.type != 'BSDF_PRINCIPLED':
            continue
        bc = node.inputs.get('Base Color')
        if not bc or not bc.is_linked:
            continue
        src = bc.links[0].from_node
        if src.type == 'TEX_IMAGE' and src.image:
            uv_name = None
            vec_in = src.inputs.get('Vector')
            if vec_in and vec_in.is_linked:
                uv_node = vec_in.links[0].from_node
                if uv_node.type == 'UVMAP':
                    uv_name = uv_node.uv_map
            return src.image, uv_name
    return None, None


def _colors_from_texture(obj, mesh):
    """Per-face color by sampling the base-color texture at face-centre UVs."""
    image, uv_name = _find_base_color_image(obj.active_material)
    if image is None:
        return None

    uv_layer = mesh.uv_layers.get(uv_name) if uv_name else None
    if uv_layer is None:
        uv_layer = mesh.uv_layers.active
    if uv_layer is None:
        return None

    n_loops = len(mesh.loops)
    n_faces = len(mesh.polygons)

    raw_uv = np.empty(n_loops * 2, dtype=np.float32)
    uv_layer.data.foreach_get('uv', raw_uv)
    loop_uvs = raw_uv.reshape(n_loops, 2)

    fi, lt = _face_loop_map(mesh)
    uv_sum = np.zeros((n_faces, 2), dtype=np.float64)
    np.add.at(uv_sum, fi, loop_uvs)
    face_uvs = (uv_sum / lt[:, None]).astype(np.float32)

    w, h = image.size
    pixels = np.array(image.pixels[:], dtype=np.float32).reshape(h, w, 4)

    px = (face_uvs[:, 0] * w).astype(np.int32) % w
    py = (face_uvs[:, 1] * h).astype(np.int32) % h
    return pixels[py, px, :3]


def _get_face_colors(obj, mesh, source):
    if source == 'TEXTURE':
        c = _colors_from_texture(obj, mesh)
        if c is not None:
            return c
        raise RuntimeError("No base-color texture found on the active material")
    if source == 'VERTEX_COLOR':
        c = _colors_from_vertex_attr(mesh)
        if c is not None:
            return c
        raise RuntimeError("No vertex-color attribute found")
    # AUTO
    c = _colors_from_texture(obj, mesh)
    if c is not None:
        return c
    c = _colors_from_vertex_attr(mesh)
    if c is not None:
        return c
    raise RuntimeError("No color data found (no texture or vertex colors)")


# ────────────────────────────────────────────────────────────────
# Utility
# ────────────────────────────────────────────────────────────────

def _linear_to_srgb(c):
    return c * 12.92 if c <= 0.0031308 else 1.055 * (c ** (1.0 / 2.4)) - 0.055


def _rgb_hex(r, g, b):
    sr = int(max(0, min(255, _linear_to_srgb(r) * 255 + 0.5)))
    sg = int(max(0, min(255, _linear_to_srgb(g) * 255 + 0.5)))
    sb = int(max(0, min(255, _linear_to_srgb(b) * 255 + 0.5)))
    return f"#{sr:02X}{sg:02X}{sb:02X}"


# ────────────────────────────────────────────────────────────────
# Operator
# ────────────────────────────────────────────────────────────────

SBC_PREFIX = "SBC_"


class MESH_OT_color_selection_sets(Operator):
    bl_idname = "mesh.color_selection_sets"
    bl_label = "Create Color Selection Sets"
    bl_description = (
        "Cluster face colors with k-means and create a vertex group "
        "for each cluster. Every face is assigned to exactly one group"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH'

    def execute(self, context):
        props = context.scene.sbc_props
        obj = context.active_object
        mesh = obj.data
        k = props.num_colors

        # 1. Gather per-face colours
        try:
            face_colors = _get_face_colors(obj, mesh, props.color_source)
        except RuntimeError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}

        n_faces = len(face_colors)

        # 2. Cluster
        labels, centroids = _kmeans(face_colors, k)

        # 3. Remove old SBC groups
        for vg in [g for g in obj.vertex_groups if g.name.startswith(SBC_PREFIX)]:
            obj.vertex_groups.remove(vg)

        # 4. Build per-loop lookup arrays
        loop_verts = np.empty(len(mesh.loops), dtype=np.int32)
        mesh.loops.foreach_get('vertex_index', loop_verts)
        fi, _ = _face_loop_map(mesh)
        loop_labels = labels[fi]

        # 5. Create vertex groups
        for i in range(k):
            hex_col = _rgb_hex(*centroids[i])
            count = int(np.sum(labels == i))
            vg = obj.vertex_groups.new(name=f"{SBC_PREFIX}{i + 1}_{hex_col}")
            verts = np.unique(loop_verts[loop_labels == i])
            vg.add(verts.tolist(), 1.0, 'ADD')

        self.report(
            {'INFO'},
            f"Created {k} selection sets from {n_faces:,} faces",
        )
        return {'FINISHED'}


# ────────────────────────────────────────────────────────────────
# Panel
# ────────────────────────────────────────────────────────────────

class MESH_PT_selection_by_color(Panel):
    bl_label = "Selection by Color"
    bl_idname = "MESH_PT_selection_by_color"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Color Select"

    def draw(self, context):
        layout = self.layout
        props = context.scene.sbc_props
        obj = context.active_object

        if obj is None or obj.type != 'MESH':
            layout.label(text="Select a mesh object", icon='INFO')
            return

        layout.label(text=f"Object: {obj.name}", icon='OBJECT_DATA')
        if obj.active_material:
            layout.label(text=f"Material: {obj.active_material.name}", icon='MATERIAL')

        layout.separator()
        layout.prop(props, "num_colors")
        layout.prop(props, "color_source")
        layout.separator()
        layout.operator("mesh.color_selection_sets", icon='GROUP_VERTEX')

        # Show existing sets
        sbc = [vg for vg in obj.vertex_groups if vg.name.startswith(SBC_PREFIX)]
        if sbc:
            layout.separator()
            box = layout.box()
            box.label(text="Current Sets:", icon='PRESET')
            for vg in sbc:
                box.label(text=vg.name)


# ────────────────────────────────────────────────────────────────
# Properties
# ────────────────────────────────────────────────────────────────

class SBC_Properties(PropertyGroup):
    num_colors: IntProperty(
        name="Number of Colors",
        description="How many color clusters (selection sets) to create",
        default=4,
        min=1,
        max=32,
    )
    color_source: EnumProperty(
        name="Color Source",
        description="Where to read per-face colors from",
        items=[
            ('AUTO', "Auto", "Try texture first, then vertex colors"),
            ('TEXTURE', "Texture", "Sample from base-color texture"),
            ('VERTEX_COLOR', "Vertex Color", "Average vertex-color attribute"),
        ],
        default='AUTO',
    )


# ────────────────────────────────────────────────────────────────
# Registration
# ────────────────────────────────────────────────────────────────

_classes = (
    SBC_Properties,
    MESH_OT_color_selection_sets,
    MESH_PT_selection_by_color,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.sbc_props = PointerProperty(type=SBC_Properties)


def unregister():
    del bpy.types.Scene.sbc_props
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
