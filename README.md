<p align="center">
  <img src="https://raw.githubusercontent.com/Omegadarling/BlenderSelectionByColor/main/docs/images/icon.webp" width="176" alt="Selection by Color app icon">
</p>

# Selection by Color

A Blender add-on that clusters a mesh's face colors with k-means and turns each cluster
into a vertex group, so you can select geometry by the color it renders as.

Every face lands in exactly one group, and each group is named with the hex value of its
cluster centroid — e.g. `SBC_1_#C34A2F`.

![Selection by Color clusters rendered face colors and isolates one resulting geometry selection](https://raw.githubusercontent.com/Omegadarling/BlenderSelectionByColor/main/docs/images/selection-by-color.png)

*Rendered face colors become named, reusable vertex groups that can be recalled as geometry selections.*

## Actual add-on in Blender

![Blender 5.1 in Edit Mode showing one color cluster selected on a painted mesh while the Selection by Color sidebar lists four generated SBC vertex groups](https://raw.githubusercontent.com/Omegadarling/BlenderSelectionByColor/main/docs/images/blender-screenshot.png)

*Actual Blender capture: four color clusters have become named `SBC_` vertex groups, and one generated group is recalled as a geometry selection in Edit Mode.*

- **Blender:** 4.0+
- **Location:** View3D > Sidebar (`N`) > Color Select
- **Dependencies:** none beyond Blender's bundled NumPy (k-means is implemented in-file)

## Install

1. Download `selection_by_color.py`.
2. In Blender: `Edit > Preferences > Add-ons > Install...`, pick the file, and enable
   **Mesh: Selection by Color**.
3. Open the 3D viewport sidebar with `N` and switch to the **Color Select** tab.

## Use

Select a mesh object, then:

| Setting | What it does |
| --- | --- |
| **Number of Colors** | How many clusters (and therefore vertex groups) to create, 1–32. Default 4. |
| **Color Source** | Where per-face color is read from — see below. |

Hit **Create Color Selection Sets**. Existing groups prefixed with `SBC_` are removed and
rebuilt on each run, so you can re-run freely while dialing in the cluster count.

To use a set, go to Edit Mode, pick the group in `Object Data Properties > Vertex Groups`,
and click **Select**.

### Color sources

- **Texture** — samples the Principled BSDF's base-color image at each face's centroid UV.
  Follows an explicit UV Map node if one feeds the texture, otherwise uses the active UV map.
- **Vertex Color** — averages the active color attribute across each face's loops.
- **Auto** (default) — tries the texture first, falls back to vertex colors.

## Notes

- Clustering runs on linear RGB; group names are converted to sRGB hex for readability.
- The k-means seed is fixed (`42`), so the same mesh and cluster count give the same
  grouping every run.
- Texture sampling is nearest-neighbor at the face centroid — fast, but a face spanning a
  sharp texture boundary gets whichever color sits under its center point.
