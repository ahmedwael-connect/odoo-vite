/**
 * Types mirroring `odoo_vite/ui_web/api.py` and the push envelopes.
 *
 * Contract (see ui_web/api.py):
 *  - Python method calls return either a Result envelope `{ok, message, data}`
 *    or a plain payload (list/dict/str) for pure helpers.
 *  - Python -> JS push delivers `{kind, payload}` envelopes only.
 */

export type Dict = Record<string, unknown>

export interface Result<T = unknown> {
  ok: boolean
  message: string
  data?: T
}

/** Answer shape of methods that return an inline {ok, message} (e.g. cancel). */
export interface Answer {
  ok: boolean
  message: string
}

// ------------------------------------------------------------------ payloads

export interface EventPayloads {
  message: { text: string; level: string }
  refresh: Dict
  'progress-line': { op_id: string; line: string }
  'progress-done': { op_id: string }
  'db-states': { instance_id: string; states: Dict }
  'db-report': { instance_id: string; report: Dict }
  'db-schedules': { instance_id: string; schedules: Dict[]; total: number }
  'discover-ready': { instance_id: string; entries: Dict[] }
  'modules-ready': {
    instance_id: string
    modules: Dict[]
    diff: unknown
    error: string | null
  }
  'deps-ready': Dict
  'log-search': { instance_id: string; ok: boolean; message: string; matches: unknown[] }
  'log-doctor': Dict
  'log-slow': { instance_id: string; ok: boolean; message: string; rows: Dict[] }
  'profile-ready': { instance_id: string; ok: boolean; message: string; svg: string }
  'dev-rpc': Dict
  'dev-models': Dict
  'dev-meta': Dict
  'dev-records': Dict
  'dev-crons': Dict
  'dev-watch': Dict
  'wiz-branches': Dict
  'wiz-syscheck': Dict
}

export type EventKind = keyof EventPayloads

export interface Envelope<K extends EventKind = EventKind> {
  kind: K
  payload: EventPayloads[K]
}

// ------------------------------------------------------------------- entities

export interface InstanceRow {
  id: string
  name: string
  version: string
  mode: string
  status: string
  port: number
  path: string
  log_path?: string
  conf_path?: string
  primary_db: string
  tracked_dbs: string[]
  db_user?: string
  pid: number | null
  last_error: string | null
  auto_update_modules?: string[]
}

export interface StatusRow extends Dict {
  id: string
  name: string
  status: string
  pid: number | null
  port: number
  version: string
}

export interface DbState {
  exists: boolean
  initialized: boolean
  odoo_version: string
  size?: string
  size_bytes: number
  owner?: string
}

export interface ScheduleRow {
  id: string
  cron: string
  enabled: boolean
  databases: string[]
  last_run?: string | null
  last_status?: string | null
}

export interface DiscoverEntry {
  name: string
  initialized: boolean
  odoo_major?: string
}

export interface ModuleRow {
  name: string
  summary?: string
  state?: string
  installed_version?: string
  available_version?: string
  latest_version?: string
}

export interface ConfView {
  error?: string | null
  conf_path?: string
  options?: Dict
  lines?: string[]
  common?: Dict
  addons_path?: string
  backup_path?: string | null
  description?: string
  workers?: number
  log_level?: string
  python_binary?: string
}

export interface ModelEntry {
  technical: string
  display: string
}

// ------------------------------------------------------------------ API tree

type Async<T> = Promise<T>

export interface AppApi {
  instances(): Async<InstanceRow[]>
  instance(instance_id: string): Async<InstanceRow | null>
  enterprise(instance_id: string): Async<Result>
  statuses(): Async<StatusRow[]>
  suggest_port(start?: number): Async<number>
  preferences(): Async<Dict>
  save_preferences(mode: string): Async<Result>
  pick_file(title?: string, mode?: string, pattern?: string): Async<Answer & { path?: string }>
  pick_dir(title?: string): Async<Answer & { path?: string }>
  version(): Async<string>
}

export interface LifecycleApi {
  start(instance_id: string, database?: string | null, confirm?: boolean): Async<Result>
  stop(instance_id: string): Async<Result>
  restart(instance_id: string): Async<Result>
  remove(
    instance_id: string,
    drop_db?: boolean,
    confirm?: boolean,
  ): Async<Result>
  clone(instance_id: string, new_name: string, new_port?: number | null): Async<Result>
}

export interface DatabasesApi {
  refresh_states(instance_id: string): Async<Dict>
  server_reachable(): Async<boolean>
  list_backup_files(instance_id: string): Async<Dict[]>
  discover_entries(instance_id: string): Async<Dict>
  init_db(instance_id: string, db_name: string): Async<Result>
  drop_db(instance_id: string, db_name: string): Async<Result>
  backup_db(instance_id: string, db_name: string, dest: string): Async<Result>
  restore_db(instance_id: string, dump: string, target: string): Async<Result>
  validate(instance_id: string): Async<Dict>
  track(instance_id: string, db_name: string): Async<Answer>
  untrack(instance_id: string, db_name: string): Async<Answer>
  track_many(instance_id: string, db_names: string[]): Async<Answer>
  set_primary(instance_id: string, db_name: string): Async<Answer>
  refresh_schedules(instance_id: string): Async<Answer>
  run_schedule_now(schedule_id: string): Async<Result>
  switch_db(instance_id: string, db_name: string, confirm?: boolean): Async<Result>
  sched_create(instance_id: string, payload: Dict): Async<Result>
  sched_update(schedule_id: string, payload: Dict): Async<Result>
  sched_delete(schedule_id: string): Async<Answer>
  sched_toggle(schedule_id: string, enabled: boolean): Async<Answer>
  file_delete(dump_path: string): Async<Answer>
  group_entries(entries: Dict[], instance_version: string): Async<Dict>
}

export interface ModulesApi {
  cancel(op_id: string): Async<Answer>
  refresh_modules(instance_id: string): Async<Answer>
  install(instance_id: string, names: string[], op_id?: string): Async<Result>
  update(instance_id: string, names: string[], op_id?: string): Async<Result>
  uninstall(instance_id: string, name: string, op_id?: string): Async<Result>
  update_code(instance_id: string, op_id?: string): Async<Result>
  fetch_deps(instance_id: string, name: string): Async<Answer>
  run_tests(
    instance_id: string,
    db_name: string,
    module: string,
    op_id?: string,
  ): Async<Result>
  state_category(mod: Dict): Async<string>
  state_categories(modules: Dict[]): Async<Record<string, string>>
  preview_command(
    instance_id: string,
    db_name: string,
    flag: string,
    names: string[],
  ): Async<string>
  split_deps(edges: unknown, name: string): Async<unknown[]>
}

export interface ConfigApi {
  read(instance_id: string): Async<Dict>
  save(instance_id: string, changes: Dict): Async<Result>
  restore(instance_id: string): Async<Result>
  regenerate(instance_id: string): Async<Result>
  meta_save(instance_id: string, meta: Dict): Async<Result>
  apply_addons(instance_id: string, entries: unknown[]): Async<Result>
  addons_state(instance_id: string): Async<Dict[]>
  looks_like_addons(path: string): Async<boolean>
  validate_meta(meta: Dict): Async<string | null>
  venv_status(instance_id: string): Async<Dict>
  rebuild_venv(instance_id: string, op_id?: string): Async<Result>
}

export interface LogsApi {
  tail(instance_id: string, n?: number): Async<Answer & { lines: string[] }>
  search(instance_id: string, pattern: string, level?: string | null): Async<Answer>
  doctor(instance_id: string): Async<Answer>
  slow_refresh(instance_id: string): Async<Answer>
  profile(instance_id: string, duration?: number): Async<Answer>
  format_search(matches: unknown[]): Async<string[]>
  format_doctor(findings: unknown[]): Async<string[]>
  format_slow(rows: unknown[]): Async<string[]>
  parse_duration(text: string): Async<number>
}

export interface DevToolsApi {
  rpc_connect(
    instance_id: string,
    user?: string,
    password?: string,
    remember?: boolean,
  ): Async<Answer>
  list_models(instance_id: string): Async<Answer>
  model_metadata(instance_id: string, model: string): Async<Answer>
  last_meta(instance_id: string): Async<Dict>
  cached_record(instance_id: string, record_id: number): Async<Answer>
  records_page(instance_id: string, offset: number): Async<Answer>
  rec_search(instance_id: string, field: string, op: string, value: string): Async<Answer>
  rec_page(instance_id: string, delta: number): Async<Answer>
  rec_create(instance_id: string, values: Dict): Async<Answer>
  rec_update(instance_id: string, record_id: number, values: Dict): Async<Answer>
  rec_delete(instance_id: string, record_id: number, expected: string): Async<Answer>
  cron_refresh(instance_id: string): Async<Answer>
  launch_json(instance_id: string): Async<Answer>
  open_editor(instance_id: string, editor: string): Async<Answer>
  diff_record(current: Dict, updated: Dict): Async<Dict>
  shell_start(instance_id: string, db_name?: string): Async<Result>
  shell_stop(): Async<Result>
  shell_send(line: string): Async<Result>
  shell_poll(): Async<{ running: boolean; exit_code: number | null; lines: string[] }>
  editable_fields(meta: unknown, record: unknown): Async<unknown[]>
  format_meta_line(meta: unknown): Async<string>
  format_record_label(record: Dict): Async<string>
  format_cron_line(cron: Dict): Async<string>
}

export interface WizardsApi {
  cancel(op_id: string): Async<Answer>
  load_branches(search?: string): Async<string[]>
  run_syscheck(version: string): Async<Dict[]>
  install_requirements(missing: string[], op_id?: string): Async<Result>
  provision(draft: Dict, plaintext?: boolean, op_id?: string): Async<Result>
  discard_draft(instance_id: string): Async<Answer>
  adopt_run(name: string, conf: string, community: string, overrides: Dict): Async<Result>
  scaffold_install(
    definition: Dict,
    dest: string,
    instance_id: string,
    db_name: string,
    op_id?: string,
  ): Async<Result>
  generate_password(length?: number): Async<string>
  validate_details(values: Dict): Async<string | null>
  build_draft(details: Dict, version: string): Async<Dict>
  refresh_draft(draft: Dict, details: Dict, version: string): Async<Dict>
  parse_field_lines(text: string): Async<Dict[]>
  build_scaffold_definition(values: Dict): Async<Dict>
  validate_scaffold(values: Dict, has_instances: boolean): Async<string | null>
  parse_adopt_paths(conf: string, community: string): Async<Dict>
  validate_locate(name: string, conf: string, community: string): Async<string | null>
  gap_rows(parsed: Dict, report: Dict): Async<Dict[]>
  suggest_db_name(name: string): Async<string>
  build_adopt_overrides(parsed: Dict, gap_values: Dict, db_name: string): Async<Dict>
}

export interface TransferApi {
  preview(archive: string): Async<Result>
  export_bundle(instance_id: string, dest: string): Async<Result>
  import_bundle(archive: string, new_name: string, new_port?: number | null): Async<Result>
  bundle_filename(name: string, stamp: string): Async<string>
}

export interface AuditEvent {
  ts: string
  instance_id: string
  instance_name: string
  action: string
  detail?: string
}

export interface AuditApi {
  tail(limit?: number): Async<AuditEvent[]>
}

export interface WatchApi {
  start(instance_id: string): Async<Result>
  stop(instance_id: string): Async<Result>
  status(instance_id: string): Async<Dict>
}

/** Root object exposed to JS as `window.pywebview.api`. */
export interface ApiTree {
  app: AppApi
  lifecycle: LifecycleApi
  databases: DatabasesApi
  modules: ModulesApi
  config: ConfigApi
  logs: LogsApi
  devtools: DevToolsApi
  wizards: WizardsApi
  transfer: TransferApi
  audit: AuditApi
  watch: WatchApi
}
