# PE evidence, projection math, and observation limits

Authored synthesis; references reviewed 2026-11-11.

## PE and binary evidence

Keep file offsets, relative virtual addresses (RVAs), and runtime virtual addresses distinct. The section table relates file ranges to virtual ranges; alignment and zero-filled virtual tails matter. Check machine type, optional-header format, directory bounds, and section bounds before interpreting imports or relocations. Record file hashes and module load bases; addresses measured in one build are not universal. [Microsoft PE format](https://learn.microsoft.com/en-us/windows/win32/debug/pe-format).

For an import or relocation investigation, retain the exact binary, parser version, source range, and interpreted fields. Flag overlapping sections, truncated tables, unsupported types, and missing symbol evidence instead of inventing a layout. Static headers describe an image, not every runtime mapping decision.

## Projection convention checklist

Establish vector orientation, matrix layout, handedness, world units, and clip-depth convention from the engine or a controlled scene. Transform to homogeneous clip coordinates, reject invalid or near-zero w, divide to normalized device coordinates, then apply the viewport transform. The viewport's origin and vertical axis determine the final y mapping. [Microsoft viewports and clipping](https://learn.microsoft.com/en-us/windows/win32/direct3d9/viewports-and-clipping) documents the Direct3D context; other APIs may differ.

Use known points at the center, edges, behind the camera, and near the clipping plane. Recompute after resize. This is a validation method, not a build-specific matrix offset.

## Observation and anti-cheat claims

Windows exposes image-load callback registration through `PsSetLoadImageNotifyRoutine`. This documents an observation surface, not the implementation of a named anti-cheat product. [Microsoft image-load notifications](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/ntddk/nf-ntddk-pssetloadimagenotifyroutine).

Absence of a user-mode hook does not prove absence of observation elsewhere. Record which events were actually measured, on which OS/product build, with which collection tools. No evasion technique, invisibility guarantee, or tested anti-cheat compatibility is provided by this reference pack.
