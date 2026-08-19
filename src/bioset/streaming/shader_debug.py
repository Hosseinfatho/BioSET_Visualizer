"""Dev utility: dump the fragment shader VTK actually generates.

The generated source is the ground truth for shader-replacement work — the
replacement keys are verbatim substring matches against it, and a key that
drifts produces NO error, the effect just silently disappears. Ported from the
integrated-heatmap experiment (final.py).

Mechanism: inject a deliberate syntax error via a //VTK::Base::Dec replacement
so VTK prints the full failing source with line numbers, capture VTK's output
to a file, regex-reassemble the numbered lines, strip the garbage line.

Usage (env-gated in app startup, or from a debug console):
    from bioset.streaming.shader_debug import dump_generated_fragment_shader
    dump_generated_fragment_shader(shader_property, render_window, Path("out"))
"""
from __future__ import annotations

import re
from pathlib import Path

import vtk


def extract_fragment_shader(vtk_log: str):
    """Reassemble the largest numbered-line shader listing from a VTK log."""
    first_line_pattern = re.compile(r"vtkShaderProgram\s*\([^)]*\):\s*(\d+):\s?(.*)$")
    numbered_line_pattern = re.compile(r"^\s*(\d+):\s?(.*)$")

    blocks = []
    current_block = []
    expected_line_number = 1

    for log_line in vtk_log.splitlines():
        match = first_line_pattern.search(log_line)
        if match is None:
            match = numbered_line_pattern.match(log_line)
        if match is None:
            if current_block:
                blocks.append(current_block)
                current_block = []
                expected_line_number = 1
            continue

        line_number = int(match.group(1))
        source_line = match.group(2)

        if line_number == 1:
            if current_block:
                blocks.append(current_block)
            current_block = [source_line]
            expected_line_number = 2
        elif current_block and line_number == expected_line_number:
            current_block.append(source_line)
            expected_line_number += 1
        elif current_block:
            blocks.append(current_block)
            current_block = []
            expected_line_number = 1

    if current_block:
        blocks.append(current_block)
    if not blocks:
        return None

    def block_score(block):
        source = "\n".join(block)
        return (
            int("GARBAGE_BREAKS_COMPILE" in source),
            int("in_volume" in source and "castRay" in source),
            len(block),
        )

    shader_source = "\n".join(max(blocks, key=block_score))
    if "in_volume" not in shader_source or "castRay" not in shader_source:
        return None

    shader_source = re.sub(
        r"^\s*GARBAGE_BREAKS_COMPILE\s*$\n?", "", shader_source, flags=re.MULTILINE)
    return shader_source.rstrip() + "\n"


def dump_generated_fragment_shader(
    shader_property,
    render_window,
    out_dir: Path,
    stem: str = "volume_fragment_shader",
) -> Path | None:
    """Write the generated fragment shader to `<out_dir>/<stem>.glsl`.

    Temporarily breaks compilation to force VTK to print the source, then
    removes the injected garbage. The caller is responsible for re-rendering
    afterwards (the next Render() recompiles cleanly). Returns the dump path
    or None on failure.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / f"{stem}.log"
    dump_path = out_dir / f"{stem}.glsl"
    if log_path.exists():
        log_path.unlink()

    shader_property.AddFragmentShaderReplacement(
        "//VTK::Base::Dec", True, "GARBAGE_BREAKS_COMPILE\n//VTK::Base::Dec", False)

    use_vtk_logger = hasattr(vtk, "vtkLogger") and vtk.vtkLogger.IsEnabled()
    previous_output_window = None

    if use_vtk_logger:
        vtk.vtkLogger.LogToFile(
            str(log_path), vtk.vtkLogger.TRUNCATE, vtk.vtkLogger.VERBOSITY_ERROR)
    else:
        previous_output_window = vtk.vtkOutputWindow.GetInstance()
        file_output_window = vtk.vtkFileOutputWindow()
        file_output_window.SetFileName(str(log_path))
        file_output_window.SetFlush(1)
        file_output_window.AppendOff()
        vtk.vtkOutputWindow.SetInstance(file_output_window)

    try:
        shader_property.Modified()
        render_window.Render()
    finally:
        if use_vtk_logger:
            vtk.vtkLogger.EndLogToFile(str(log_path))
        elif previous_output_window is not None:
            vtk.vtkOutputWindow.SetInstance(previous_output_window)
        # Remove the injected garbage so the next rebuild is clean. There is
        # no per-key removal API; the caller's manager reinstalls after a
        # ClearAll, so clear here and let the caller rebuild.
        shader_property.ClearAllFragmentShaderReplacements()
        shader_property.Modified()

    if not log_path.exists():
        print(f"[shader_debug] dump failed: VTK did not create {log_path}")
        return None

    shader_source = extract_fragment_shader(
        log_path.read_text(encoding="utf-8", errors="replace"))
    if shader_source is None:
        print(f"[shader_debug] dump failed: no complete fragment shader in {log_path}")
        return None

    dump_path.write_text(shader_source, encoding="utf-8")
    print(f"[shader_debug] generated fragment shader -> {dump_path} "
          f"({len(shader_source.splitlines())} lines); raw log: {log_path}")
    return dump_path
