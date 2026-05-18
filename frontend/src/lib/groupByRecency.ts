// Group items by recency buckets — mirrors how Claude Desktop and
// Perplexity show chat history.

export type RecencyBucket =
  | "Today"
  | "Yesterday"
  | "Last 7 days"
  | "Last 30 days"
  | "Older";

const BUCKET_ORDER: RecencyBucket[] = [
  "Today",
  "Yesterday",
  "Last 7 days",
  "Last 30 days",
  "Older",
];

export function bucketFor(iso?: string | null): RecencyBucket {
  if (!iso) return "Older";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "Older";
  const now = Date.now();
  const startOfToday = new Date();
  startOfToday.setHours(0, 0, 0, 0);
  const startOfYesterday = new Date(startOfToday);
  startOfYesterday.setDate(startOfYesterday.getDate() - 1);

  if (then >= startOfToday.getTime()) return "Today";
  if (then >= startOfYesterday.getTime()) return "Yesterday";
  const diffDays = (now - then) / 86400000;
  if (diffDays < 7) return "Last 7 days";
  if (diffDays < 30) return "Last 30 days";
  return "Older";
}

export function groupByRecency<T>(
  items: T[],
  getDate: (item: T) => string | undefined | null,
): Array<{ label: RecencyBucket; items: T[] }> {
  const buckets = new Map<RecencyBucket, T[]>();
  for (const item of items) {
    const b = bucketFor(getDate(item));
    if (!buckets.has(b)) buckets.set(b, []);
    buckets.get(b)!.push(item);
  }
  return BUCKET_ORDER.filter((b) => buckets.has(b)).map((label) => ({
    label,
    items: buckets.get(label)!,
  }));
}
