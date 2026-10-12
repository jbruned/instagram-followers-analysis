// What the server sends, as the page uses it. The server's shapes are in api.py and analysis.py.

export type Flag = "follow" | "unfollow";
export type AccountStatus = "unchecked" | "live" | "possibly_unavailable" | "unavailable" | "error";
export type RelationState = "mutual" | "fan" | "following" | "none";

/** One account in a list. */
export interface Row {
  username: string;
  key: string;
  ts: number | null;
  context: string;
  status: AccountStatus;
  checked_at: string | null;
  first_unavailable_at: string | null;
  full_name: string | null;
  is_private: boolean | null;
  is_verified: boolean | null;
  last_post_at: string | null;
  no_posts: boolean;
  inactive: boolean;
  followers_count: number | null;
  following_count: number | null;
  high_ratio: boolean;
  ignored: boolean;
  flag: Flag | null;
  flag_state: "pending" | "done" | null;
  flagged_at: string | null;
  flag_action: Flag | null;
  snoozed_until: string | null;
  close_friend: boolean;
  avatar_url: string | null;
}

export interface Segment {
  state: RelationState;
  from: string;
  to: string;
  /** set by the page: the stretch that runs to today */
  open?: boolean;
}

export interface RelationEvent {
  kind: "they_followed" | "they_unfollowed" | "you_followed" | "you_unfollowed";
  from: string;
  to: string;
  ts: number | null;
}

export interface Timeline {
  segments: Segment[];
  events: RelationEvent[];
  dates: string[];
}

export interface Relation {
  following: boolean;
  follows_you: boolean;
  pending_sent: boolean;
  pending_received: boolean;
  follows_you_since: number | null;
  following_since: number | null;
  you_unfollowed_at: number | null;
}

/** An account's window: its row plus how you are related to it. */
export interface Profile extends Row {
  relation: Relation;
  timeline: Timeline;
  incomplete: boolean;
}

export interface SnapshotInfo {
  id: number;
  owner: string;
  taken_at: string;
  partial: string | null;
  partial_since: string | null;
  previous_taken_at: string | null;
}

export interface Summary {
  snapshot: SnapshotInfo;
  headline: string;
  counts: Record<string, number>;
  totals: { mutual: number; not_following_back: number; fans: number };
  following: number;
  followers: number;
  kinds_present: string[];
  snapshot_count: number;
  old_snapshot_days: number;
}

export interface Snapshot {
  id: number;
  owner: string;
  taken_at: string;
  partial: string | null;
  ingested_at: string;
}

export interface Owner {
  owner: string;
  label: string | null;
  snapshots: number;
}

export interface ListPage {
  total: number;
  offset: number;
  items: Row[];
}

export interface HistoryEvent {
  username: string;
  key: string;
  ts: number | null;
  previous_ts?: number;
  previous_username?: string;
  label?: string;
}

export interface HistoryGroup {
  from_snapshot: number;
  to_snapshot: number;
  from_date: string;
  to_date: string;
  partial: string | null;
  counts: Record<string, number>;
  events: Record<string, HistoryEvent[]>;
}

export interface SettingField {
  key: string;
  label: string;
  group: string;
  unit: string;
  type: "integer" | "number";
  min: number;
  max: number;
  help: string;
  default: number;
  value: number;
  customised: boolean;
}

export interface CheckerTier {
  label: string;
  total: number;
  checked: number;
  due: number;
}

export type CheckerStatus =
  | { enabled: false; state: "disabled" }
  | {
      enabled: true;
      state: string;
      paused: boolean;
      cooldown_until: string | null;
      cooldown_reason: string | null;
      requests_24h: number;
      daily_cap: number;
      viewport_check_days: number;
      viewport_dwell_seconds: number;
      queue_size: number;
      tiers: Record<string, CheckerTier>;
      last_result: { username: string; outcome: string; detail: string | null; at: string } | null;
      checks_done: number;
    };

export interface ImportedSnapshot {
  snapshot_id: number;
  source: string;
  owner: string;
  taken_at: string;
  partial: string | null;
}

export interface PartialItem {
  name: string;
  reason: string;
  first: boolean;
  previous: string | null;
}

export interface UploadResult {
  found: number;
  imported: ImportedSnapshot[];
  skipped: number;
  errors: string[];
  partial: PartialItem[];
  needs_owner: { name: string }[];
}

export interface WatchStatus {
  imports: number;
  last_import: ImportedSnapshot | null;
}

/** A file picked or dropped, with its path inside a dropped folder when there is one. */
export interface UploadItem {
  file: File;
  path?: string;
}

/** An error from the server, with its explanation when it gave one. */
export interface ApiError extends Error {
  detail?: string;
}

/** A child of a DOM element built with `el`: text, a node, a list of them, or nothing. */
export type Child = string | number | boolean | null | undefined | Node | Child[];

/** What you can decide about an account ("none" clears the decision). */
export type Decision = "ignored" | "snoozed" | "follow" | "unfollow" | "none";

declare global {
  interface Window {
    /** A handle for the browser tests, and for poking at the page from the console. */
    followAudit: {
      banner: (text: string, isError?: boolean, onClick?: (() => unknown) | null) => void;
      loadSummary: () => Promise<void>;
      reloadLoaded: () => Promise<void>;
      openProfile: (username: string) => Promise<void>;
      state: unknown;
    };
  }
}
