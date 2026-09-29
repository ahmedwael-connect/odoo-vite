# Slint Thread Safety (PSS-4): beta-binding rules + incident record

`slint` 1.18b1 (pinned in `pyproject.toml`; newest available — the Python
API never left beta) aborts the process intermittently when Slint values
coexist with worker threads + allocation/GC pressure. Signature:
`slint_python::value::PyStruct is unsendable, but sent to another thread`
(pyo3 pyclass.rs:1068), or SIGABRT under GC in alloc-heavy frames, with
moving crash sites. Pure-Slint and thread-free paths are clean, every
time; the Qt frontend never exhibits it.

Status: root cause is in the binding, NOT app code. The architecture
below makes the suite deterministic (386 green, repeated runs); live
X11 runs are the UI integration gate.

## Rules (law until the binding is fixed)

1. **Slint values and worker threads never coexist in tests.**
   Bridge tests stub `asyncio.run_coroutine_threadsafe` (record, never
   execute — zero worker threads exist); ops tests run real core with
   zero Slint values. Verified control: threads + subprocess + sqlite +
   GC with no Slint is clean; Slint + workers is not.
2. **No first-imports in workers** (Qt PSQ-4 rule, kept): `ui_slint/`
   imports everything at module top; the bridge pre-imports the core
   surface workers may touch.
3. **Slint-held callbacks are weak** (`_wcb`/`_dcb` partials): no
   component↔owner refcount cycles, so teardown is plain refcounting on
   the dropping thread instead of cyclic GC anywhere.
4. **Dialogs are owned + released** (`_own`/`_release`/`dismiss()`):
   terminal paths dismiss (hide + drop view ref) on the UI thread.
   Never reassign a callback from inside its own invocation (instant
   abort — verified the hard way, then fixed).
5. **In-place model sync, never replace** (`selection.sync_model`):
   unchanged rows keep identity. Never mutate `view.rows` in place —
   the getter returns a read-only wrapper; only Python-held ListModels
   accept writes.
6. **Quiescence on teardown** (`wait_idle` asserts, loop stop/join,
   executor shutdown, timer stops in `close()`).
7. **No automatic GC during Slint-value tests** (PSS-5b): dialog-result
   lambdas close over their driver while the driver holds the lambda —
   a cycle only cyclic GC can free, and CPython collects on whichever
   thread allocates past threshold (idle loop threads included), which
   aborts in `__clear__` when the clearer isn't the owner thread.
   Bridge tests disable auto-GC for the test body (refcounting still
   frees acyclic trash) and `gc.collect()` explicitly on the main thread
   after every loop is joined. Same quiescent-collect shape as the
   production `gc.disable()` proposal, but here the suite *proves* it
   (regressed to ~1/8 abort rate before, 3/3 + full-suite ×2 after).

## Incident record (condensed)

~30 controlled experiments. Proven innocent, with evidence: shiboken
import hook (crash reproduces with zero Qt in-process), timers, two-way
bindings, nested models, ComboBox/Table widgets, keyring/dbus,
subprocess-vs-sqlite, `run_event_loop` (hangs headless — unusable for
tests). Proven guilty: Slint struct/model values alive + asyncio
executor threads allocating + any GC trigger. Real bugs the hunt
*also* surfaced and fixed: `sched_row or -1` unselectable row 0,
spawn-during-close races, stale-probe double counting, dialog dismiss
reentrancy, `ToastDriver` timer leak on dismiss.

Open production question — SHIPPED in PSS-9: the app could not boot
without it (consistent startup abort in this environment), so
`gc.disable()` at entry + explicit owner-thread collects on dialog
release and teardown is the production posture, not a soak-gated
experiment. Refcounting still frees acyclic trash immediately; cyclic
trash is freed only on the UI thread at quiescence. Watch memory over
long sessions; revisit if Slint fixes the binding.

## Upstream report draft

> slint-python 1.18b1: intermittent SIGABRT
> (`PyStruct is unsendable, but sent to another thread`, pyclass.rs:1068)
> when Slint struct/model values are alive alongside
> `asyncio.to_thread` workers + allocation/GC. Pure single-threaded
> programs are clean, including 30-round model-mutation stress; adding
> worker threads reproduces within dozens of iterations, no Qt involved.
> Full bisect log available on request.
