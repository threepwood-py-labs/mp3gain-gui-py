# AGENTS.md — mp3gain-gui-py

Developer notes, implementation decisions, and validation procedures.

---

## Project overview

PySide6 port of MP3Gain (Glen Sawyer / David Robinson) with a vendored C runtime backend.
Uses editable legacy C sources (`src/c/legacy/mp3gain-1_5_2-src`) plus Python orchestration for the GUI/CLI surface.
Target feature parity: VB6 GUI v1.2.5.

- **Package:** `mp3gain_gui_py`
- **Python:** 3.13
- **UI toolkit:** PySide6 ≥ 6.10.2
- **Template:** _py_template v1.7.2

---

## Legacy CLI runtime policy

- `src/mp3gain_gui_py/legacy_cli.py` is C-library-backed by default via `src/mp3gain_gui_py/_c_backend`.
- The authoritative runtime C sources are vendored under `src/c/legacy/mp3gain-1_5_2-src` and built with MinGW.
- Python runtime orchestration must call the DLL shim for analyze/apply/undo/tag operations.
- The pure-Python runtime implementation is reference-only, and is not maintained or tested.
- There is no supported Python runtime fallback path; C backend initialization failure is fatal for runtime execution.
- Do not delegate runtime flow to external `mp3gain.exe`.
- `C:/bin/mp3gain-win-1_2_5/mp3gain.exe` is oracle-only for parity/validation tooling (for example `scripts/parity_*`), not a runtime backend.

---

## Source references

| What | Path |
|------|------|
| ReplayGain DSP (C) | `C:/prj/misc/mp3gain/mp3gain-1_5_2-src/gain_analysis.c` |
| MP3 frame manipulation (C) | `C:/prj/misc/mp3gain/mp3gain-1_5_2-src/mp3gain.c` |
| APEv2 tag format (C) | `C:/prj/misc/mp3gain/mp3gain-1_5_2-src/apetag.c` |
| ID3v2 tag format (C) | `C:/prj/misc/mp3gain/mp3gain-1_5_2-src/id3tag.c` |
| VB6 UI spec | `C:/prj/misc/mp3gain/mp3gain-win-gui-1_2_5-src/frmMain.frm` |
| Reference binary | `C:/bin/mp3gain-win-1_2_5/mp3gain.exe` |
| Reference GUI | `C:/bin/mp3gain-win-1_2_5/MP3GainGUI.exe` |

### Legacy pointer traceability

- Canonical map: `docs/architecture/legacy_pointer_map.md`
- Inline contract token: `LEGACY_PTR:<UPPER_SNAKE_ID>`
- Use `rg -n "LEGACY_PTR:" src/mp3gain_gui_py scripts` to navigate Python-to-legacy anchors quickly.

---

## Validated reference values

Obtained by running `mp3gain.exe -q -o` on `c:\tmp\mp3\original\*.mp3`:

```
File   dB gain  steps  max-amp       max-gain  min-gain
1.mp3   -9.21    -6    33973.51        210        82
2.mp3   -6.29    -4    32255.55        210        76
3.mp3   -9.10    -6    35503.78        210        38
4.mp3   -9.02    -6    34830.25        210        47
5.mp3  -11.21    -7    34677.88        188       118
6.mp3   -6.99    -5    38316.25        210       103
7.mp3   -9.85    -7    35637.46        187       117
Album  -9.35
```

Pre-processed to 89 dB reference output is at `c:\tmp\mp3\89db\`.

---

## Module structure

```
src/mp3gain_gui_py/
  _c_backend/
    build.py          MinGW DLL build orchestration
    runtime.py        ctypes bindings to shim exports
  _engine/
    coefficients.py   IIR tables mirrored from gain_analysis.c (reference only)
    filter_py.py      Legacy pure-Python filter path (reference only, not maintained/tested)
    filter_np.py      NumPy-accelerated legacy filter path (reference only, not maintained/tested)
    replaygain.py     Legacy analyzer retained as reference-only code
    pcm_reader.py     PCM decode helpers retained as reference-only code
  _mp3/
    frame_parser.py   FrameHeader + global_gain bit offsets
    crc.py            CRC-16 for protected frames
    file_info.py      scan_file (min/max gain), scan_max_amplitude
    gain_writer.py    apply_gain_change, undo_gain_change
  _tags/
    formats.py        Tag key constants + format/parse helpers
    reader.py         TagData + read_tags (APEv2 -> ID3 fallback)
    writer.py         write_tags, delete_tags
  _settings/
    registry.py       Key constants base class
    storage.py        SettingsStorage wrapping QSettings
    models.py         UiSettings, OpsSettings, SessionSettings (frozen)
    normalize.py      Value coercion helpers
    domains.py        UiSettingsDomain, OpsSettingsDomain, SessionSettingsDomain
    manager.py        SettingsManager with _delegate_property pattern
  _model/
    file_entry.py     FileEntry mutable dataclass
    file_list_model.py  FileListModel(QAbstractTableModel), 8 columns
  _workers/           (Phase 8 — pending)
  ui/                 (Phases 10-11 — pending)
  app_controller.py   (Phase 9 — pending)
```

---

## ReplayGain DSP implementation

### Algorithm (faithful port of gain_analysis.c)

1. Decode MP3 to PCM using `miniaudio.stream_file`.
2. Feed interleaved stereo chunks through two cascaded IIR filters per channel:
   - **Yule filter** — 10th-order equal-loudness weighting
   - **Butterworth filter** — 2nd-order high-pass
3. For each 50 ms RMS window accumulate `sum(sample²)` per channel.
4. Compute window power: `(lsum + rsum) / totsamp * 0.5`
5. Map to histogram bin: `ival = clamp(int(100 * 10 * log10(power + 1e-37)), 0, 11999)`
6. 95th-percentile scan from top of histogram → `PINK_REF - i / STEPS_PER_DB`

### Constants

```python
STEPS_PER_DB = 100
MAX_DB       = 120
PINK_REF     = 64.82
MAX_ORDER    = 10   # Yule filter order
RMS_WINDOW_TIME_MS = 50
RMS_PERCENTILE     = 0.95
```

### Critical implementation note — PCM scale

The histogram formula is calibrated for **PCM-16 integer scale** (−32768..32767),
matching the C reference.  `miniaudio` yields normalised float32 (−1..1).

**Fix applied in `pcm_reader.py` `decode_to_stereo_chunks`:**

```python
left  = [v * 32768.0 for v in samples[0::2]]
right = [v * 32768.0 for v in samples[1::2]]
```

Without this scaling all histogram bins are zero → `_analyze_result` returns
`PINK_REF (64.82)` for every file regardless of content.

`scan_max_amplitude` in `file_info.py` uses `iter_pcm_chunks` directly (float32)
and applies `peak * 32768.0` separately — this is intentional and correct.

### IIR filter signature

```python
def filter_yule(
    input_buf: list[float],   # pre_buffer(10) + current_samples
    output_pre: list[float],  # last 10 output values
    n_samples: int,
    kernel: tuple[float, ...],  # 21 elements: b0,a1,b1,...,a10,b10
) -> list[float]:             # n_samples new outputs only
```

Pre-buffers are updated by caller between calls to maintain continuity.
The `1e-10` denormal-prevention offset is applied in `filter_yule` only,
not in `filter_butter` (mirrors gain_analysis.c exactly).

---

## MP3 frame format

### global_gain bit positions (from `scanFrameGain()` / `changeGain()` in mp3gain.c)

MPEG1 frame layout after sync+header (4 bytes) + optional CRC (2 bytes):

```
Side-info offset from frame start:
  MPEG1 stereo:  byte 4 (or 6 if CRC-protected)
  MPEG1 mono:    byte 4 (or 6)
  MPEG2 stereo:  byte 4 (or 6)
  MPEG2 mono:    byte 4 (or 6)

global_gain fields (each 8 bits):
  MPEG1 stereo:  4 fields, one per (granule, channel) at bit offsets 41, 100, 159, 218
  MPEG1 mono:    2 fields at bit offsets 41, 100
  MPEG2 stereo:  2 fields (1 granule) at bit offsets 21, 80  (block_bits=63)
  MPEG2 mono:    1 field at bit offset 21
```

`global_gain_offsets(header)` returns `list[tuple[byte_offset, bit_offset]]`
relative to frame start (i.e. already accounting for header + CRC bytes).

---

## Validation procedure

### Quick analysis check

```bash
# From project root
hatch run python scripts/compare_analysis.py
```

Expected output (tolerance ±0.15 dB, steps must match exactly):

```
File          Volume    RawdB  Steps  AppliedDB       MaxAmp  MaxG  MinG   vs ref raw_db  vs ref steps
--------------------------------------------------------------------------------------------------------------
1.mp3           98.2   -9.200     -6       -9.0     32768.00   210     0          +0.010          True  OK
2.mp3           95.2   -6.210     -4       -6.0     32257.00   210     0          +0.080          True  OK
3.mp3           98.1   -9.110     -6       -9.0     32768.00   210     0          -0.010          True  OK
4.mp3           98.0   -8.980     -6       -9.0     32768.00   210     0          +0.040          True  OK
5.mp3          100.2  -11.220     -7      -10.5     32768.00   210     0          -0.010          True  OK
6.mp3           96.0   -7.000     -5       -7.5     32768.00   210     0          -0.010          True  OK
7.mp3           98.8   -9.770     -7      -10.5     32768.00   210     0          +0.080          True  OK

OK  All results match reference within tolerance.
```

Note: min_gain column shows 0 for all files (frame parser bug — tracked separately).
ReplayGain dB and steps are confirmed correct.

### Full lint + test

```bash
cd C:/prj/aidev/py/mp3gain-gui-py
hatch run test
hatch run lint:types    # basedpyright strict — zero errors
hatch run lint:check    # ruff — zero errors
```

### Manual gain application check

Apply Python gain to original files, save to `c:\tmp\mp3\89dbcodex\`, compare
byte-level with `c:\tmp\mp3\89db\` (processed by reference MP3GainGUI.exe to 89 dB):

```bash
# TODO: implement apply_gain_change() validation script
# scripts/apply_and_compare.py
```

Parity test runs use `c:\tmp\mp3\89dbcodex\` as the output working directory.

Reference binary for cross-checking gain application:

```bash
c:\bin\mp3gain-win-1_2_5\mp3gain.exe -q -o c:\tmp\mp3\original\*.mp3
```

---

## Settings system

Mirrors the `many-panelz-explorer` pattern exactly.

`_delegate_property(domain_attr, name)` creates a `property` that forwards
get/set to the named attribute on the nested domain object:

```python
class SettingsManager(SettingsRegistry):
    target_volume_db = _delegate_property("ui", "target_volume_db")
    wrap_gain        = _delegate_property("ops", "wrap_gain")
    column_widths    = _delegate_property("session", "column_widths")
```

Default values live in `registry.py` (class constants on `SettingsRegistry`).

---

## Qt conventions

- All key widgets call `self.setObjectName("...")` in `__init__` — never as a
  class attribute (conflicts with `QObject.objectName` method).
- `rowCount`/`columnCount`/`data` overrides accept
  `QModelIndex | QPersistentModelIndex` (both from `PySide6.QtCore`).
- Background workers: pure Python threads (`threading.Thread(daemon=True)`),
  no Qt in worker code; results relayed via `QMetaObject.invokeMethod` with
  `QueuedConnection` through `WorkerBridge(QObject)`.
- All slots decorated with `@safe_slot` from `threep_commons`.

---

## Remaining implementation phases

| Phase | What | Status |
|-------|------|--------|
| 1 | Dependencies + coefficients | Done |
| 2 | ReplayGain DSP engine | Done — validated |
| 3 | PCM reader | Done — validated |
| 4 | MP3 frame manipulation | Done (min/max scan has known bug) |
| 5 | Tag I/O | Done |
| 6 | Settings system | Done |
| 7 | Data model | Done |
| 8 | Worker types + bridge | Pending |
| 9 | AppController + entry point | Pending |
| 10 | Main window + coordinators | Pending |
| 11 | Dialogs | Pending |
| 12 | Wire everything | Pending |
| 13 | Integration tests + polish | Pending |

---

## Known bugs / TODO

- **frame_parser.py — min_gain scan returns 0**: `scan_file()` reports `min_gain=0`
  for all files (reference: 38–118). The `global_gain_offsets()` logic or
  `_peek8_bits()` byte math may be off for some MPEG configurations.
  Does not affect ReplayGain analysis output.

- **compare_analysis.py — Unicode on Windows console**: Replace `✓`/`✗` with
  ASCII equivalents when stdout encoding is not UTF-8. Fixed in current version.

---

## Progress log 2026-03-09

### Implemented this session

- Added parity tooling scripts:
  - `scripts/parity_common.py`
  - `scripts/parity_oracle_snapshot.py`
  - `scripts/parity_build_codex_89db.py`
  - `scripts/parity_compare.py`
- Added optional integration smoke test:
  - `tests/integration/test_parity_smoke.py`
- Reworked `scripts/compare_analysis.py` to be analysis-only and to validate min/max global_gain against reference.
- Implemented first-frame Xing/Info skip behavior in scan/write paths.
- Updated worker logic:
  - `AnalyzeWorker` now uses MP3Gain-style semantics (`track_gain_db = raw_db`, `volume_db = target - raw_db`).
  - `GainWorker` now calls gain APIs with correct signatures and uses file tags for apply/undo values.
- Fixed tag gain formatter bug in `src/mp3gain_gui_py/_tags/formats.py` (`+9.6f` format spec).

### Validation artifacts

- Oracle snapshot:
  - `c:/tmp/pycompa/parity_oracle_snapshot.json`
- Build report:
  - `c:/tmp/pycompa/parity_build_codex_89db.json`
- Parity compare report:
  - `c:/tmp/pycompa/parity_compare_report.json`
- Long analysis log:
  - `c:/tmp/pycompa/compare_analysis_after_patch.log`

### Current parity state

- `scripts/parity_compare.py` currently reports `FAIL`.
- Byte-identical parity vs `c:/tmp/mp3/89db` is not yet achieved (0/7 identical).
- `mp3gain.exe -q -o` outputs are very close but not exact for some `dB gain` and `max amplitude` values.
- Global gain min/max scan improved (files 1-4 match), but files 5-7 still differ from reference.
