import { fetchThreadsProfileHotMetrics, getLiveSentimentBrowserAuthProfileBinding, refreshSentimentBrowserCookiesForPlatform } from "@/lib/sentiment-hot-importer";
import { listPersonaArchives, updatePersonaArchiveProfile } from "@/lib/persona-archives";

function normalizeThreadsUsername(value: unknown): string {
  return String(value || "")
    .trim()
    .replace(/^https?:\/\/(?:www\.)?threads\.(?:net|com)\//i, "")
    .replace(/^@/, "")
    .split(/[/?#\s]/)[0]
    .trim();
}

function hotMetricKey(username: string): string {
  return `threads:${normalizeThreadsUsername(username).toLowerCase()}`;
}

function hasUsableMetrics(metrics: any): boolean {
  const scannedPosts = Number(metrics?.scannedPosts || 0);
  return scannedPosts > 0 || ["followers", "following", "recentViews", "posts", "likes", "comments", "reposts", "shares", "views"]
    .some((field) => typeof metrics?.[field] === "number");
}

function isCompleteMetrics(metrics: any): boolean {
  const scannedPosts = Number(metrics?.scannedPosts || 0);
  return metrics?.complete === true
    && metrics?.scope === "authenticated_full_profile"
    && scannedPosts > 0
    && Array.isArray(metrics?.postMetrics)
    && metrics.postMetrics.length >= scannedPosts;
}

async function main() {
  const targetId = process.argv.find((arg) => arg.startsWith("--archive-id="))?.slice("--archive-id=".length) || "";
  const archives = await listPersonaArchives();
  const targets = targetId ? archives.filter((archive) => archive.id === targetId) : archives;
  const refreshAuth = await refreshSentimentBrowserCookiesForPlatform("threads").catch((error: any) => ({
    ok: false,
    message: error instanceof Error ? error.message : String(error || "unknown"),
  }));
  const auth = await getLiveSentimentBrowserAuthProfileBinding("threads").catch((error: any) => ({
    ok: false,
    message: error instanceof Error ? error.message : String(error || "unknown"),
  } as any));
  const results: any[] = [];

  for (const archive of targets) {
    const setup: any = archive.setup || {};
    const accounts = setup.accountManagement || {};
    const username = normalizeThreadsUsername(accounts?.threads?.handle);
    if (!username) {
      results.push({ archiveId: archive.id, name: archive.name, ok: false, skipped: true, message: "未绑定 Threads 用户名" });
      continue;
    }
    if (!auth.ok) {
      results.push({ archiveId: archive.id, name: archive.name, username, ok: false, message: auth.message || refreshAuth.message || "Threads 授权无效，请先在后台授权中心更新 Cookie" });
      continue;
    }
    try {
      const metrics: any = await fetchThreadsProfileHotMetrics(username);
      const key = hotMetricKey(username);
      const existingHotMetrics = setup.hotMetrics || {};
      const previousMetrics = existingHotMetrics[key] || {};
      const usable = hasUsableMetrics(metrics);
      const complete = isCompleteMetrics(metrics);
      const nextMetric = complete
        ? {
            ...previousMetrics,
            platform: "threads",
            username: metrics.username || username,
            followers: metrics.followers,
            following: metrics.following,
            recentViews: metrics.recentViews,
            posts: metrics.posts,
            likes: metrics.likes,
            comments: metrics.comments,
            reposts: metrics.reposts,
            shares: metrics.shares,
            views: metrics.views,
            viewResolvedPosts: metrics.viewResolvedPosts,
            viewMissingPosts: metrics.viewMissingPosts,
            scannedPosts: metrics.scannedPosts,
            postMetrics: Array.isArray(metrics.postMetrics) ? metrics.postMetrics : previousMetrics.postMetrics,
            complete: true,
            scope: "authenticated_full_profile",
            refreshedAt: metrics.refreshedAt,
            error: undefined,
          }
        : {
            ...previousMetrics,
            platform: "threads",
            username: metrics.username || username,
            complete: false,
            scope: metrics.scope,
            refreshedAt: metrics.refreshedAt,
            scannedPosts: metrics.scannedPosts,
            error: metrics.error || (usable ? "本次只读取到局部资料，未覆盖为完整热点数据。" : "未读取到可用热点数据。"),
          };
      await updatePersonaArchiveProfile(archive.id, {
        setup: {
          ...setup,
          accountManagement: {
            ...accounts,
            threads: {
              ...(accounts.threads || {}),
              handle: username,
              authProfileKey: auth.profileKey,
              authProfileBoundAt: new Date().toISOString(),
              updatedAt: new Date().toISOString(),
            },
          },
          hotMetrics: { ...existingHotMetrics, [key]: nextMetric },
        },
      });
      results.push({
        archiveId: archive.id,
        name: archive.name,
        username,
        ok: complete,
        partial: !complete,
        scannedPosts: metrics.scannedPosts || 0,
        postMetrics: Array.isArray(metrics.postMetrics) ? metrics.postMetrics.length : 0,
        message: complete ? "刷新完成" : nextMetric.error,
      });
    } catch (error: any) {
      results.push({ archiveId: archive.id, name: archive.name, username, ok: false, message: error instanceof Error ? error.message : String(error || "刷新失败") });
    }
  }

  console.log(JSON.stringify({
    ok: results.some((item) => item.ok),
    refreshed: results.filter((item) => item.ok).length,
    partial: results.filter((item) => item.partial).length,
    skipped: results.filter((item) => item.skipped).length,
    total: results.length,
    auth: { ok: Boolean(auth.ok), message: auth.message || refreshAuth.message || "" },
    results,
  }, null, 2));
}

main().catch((error) => {
  console.error(JSON.stringify({ ok: false, message: error instanceof Error ? error.message : String(error || "refresh failed") }));
  process.exit(1);
});
