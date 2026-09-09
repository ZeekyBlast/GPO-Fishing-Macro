using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Reflection;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace GpoMacro;

/// <summary>What the latest GitHub release offers, once it is newer than this build.</summary>
public sealed record UpdateInfo(Version Version, string Tag, string DownloadUrl,
                                long Size, string PageUrl);

/// <summary>
/// Checks GitHub for a newer release and, on request, downloads that release's
/// installer and hands over to it.
///
/// The repository is pinned here rather than configurable, and a download URL
/// is refused unless GitHub itself serves it. An updater that will fetch and
/// execute a binary from wherever it is pointed is a backdoor, not a feature.
/// </summary>
public sealed class Updater
{
    private const string Owner = "ZeekyBlast";
    private const string Repo = "GPO-Fishing-Macro";

    // GitHub redirects release downloads to its object storage, so both hosts
    // are legitimate. Nothing else is.
    private static readonly string[] AllowedHosts =
        { "api.github.com", "github.com", "objects.githubusercontent.com",
          "release-assets.githubusercontent.com" };

    private static readonly HttpClient Http = CreateClient();

    private static HttpClient CreateClient()
    {
        var client = new HttpClient { Timeout = TimeSpan.FromMinutes(10) };
        // GitHub rejects requests without a User-Agent.
        client.DefaultRequestHeaders.UserAgent.Add(
            new ProductInfoHeaderValue("GPO-Fishing-Macro", CurrentVersion().ToString()));
        client.DefaultRequestHeaders.Accept.Add(
            new MediaTypeWithQualityHeaderValue("application/vnd.github+json"));
        return client;
    }

    public static Version CurrentVersion() =>
        Assembly.GetExecutingAssembly().GetName().Version ?? new Version(0, 0, 0, 0);

    public static string CurrentVersionText()
    {
        var v = CurrentVersion();
        return $"{v.Major}.{v.Minor}.{v.Build}";
    }

    /// <summary>The newer release, or null if this build is current.
    /// The out parameter carries a human-readable reason either way.</summary>
    public async Task<(UpdateInfo? Update, string Status)> CheckAsync(
        CancellationToken cancel = default)
    {
        var url = $"https://api.github.com/repos/{Owner}/{Repo}/releases/latest";
        try
        {
            using var response = await Http.GetAsync(url, cancel).ConfigureAwait(false);

            if (response.StatusCode == System.Net.HttpStatusCode.NotFound)
                return (null, "no public releases found (the repository may be private)");
            if ((int)response.StatusCode == 403)
                return (null, "GitHub rate limit reached, try again later");
            if (!response.IsSuccessStatusCode)
                return (null, $"GitHub returned {(int)response.StatusCode}");

            var body = await response.Content.ReadAsStringAsync(cancel).ConfigureAwait(false);
            using var document = JsonDocument.Parse(body);
            var root = document.RootElement;

            var tag = root.GetProperty("tag_name").GetString() ?? "";
            if (!TryParseTag(tag, out var latest))
                return (null, $"could not read the version from tag \"{tag}\"");

            var current = CurrentVersion();
            if (latest <= current)
                return (null, $"up to date (v{CurrentVersionText()})");

            var asset = root.TryGetProperty("assets", out var assets)
                ? assets.EnumerateArray().FirstOrDefault(
                    a => (a.GetProperty("name").GetString() ?? "")
                         .EndsWith(".exe", StringComparison.OrdinalIgnoreCase))
                : default;
            if (asset.ValueKind != JsonValueKind.Object)
                return (null, $"v{latest.Major}.{latest.Minor}.{latest.Build} is out, "
                              + "but it has no installer attached");

            var download = asset.GetProperty("browser_download_url").GetString() ?? "";
            if (!IsTrusted(download))
                return (null, "the release's download link is not hosted by GitHub");

            return (new UpdateInfo(latest, tag, download,
                                   asset.GetProperty("size").GetInt64(),
                                   root.GetProperty("html_url").GetString() ?? ""),
                    $"v{latest.Major}.{latest.Minor}.{latest.Build} is available");
        }
        catch (TaskCanceledException)
        {
            return (null, "the update check timed out");
        }
        catch (HttpRequestException ex)
        {
            return (null, $"could not reach GitHub: {ex.Message}");
        }
        catch (JsonException)
        {
            return (null, "GitHub sent a reply this version could not read");
        }
    }

    private static bool TryParseTag(string tag, out Version version)
    {
        var text = tag.TrimStart('v', 'V');
        // "1.2" is a legitimate tag; Version wants at least two parts and we
        // want three so comparisons against 1.0.0.0 behave.
        while (text.Count(c => c == '.') < 2) text += ".0";
        return Version.TryParse(text, out version!);
    }

    private static bool IsTrusted(string url) =>
        Uri.TryCreate(url, UriKind.Absolute, out var uri)
        && uri.Scheme == Uri.UriSchemeHttps
        && AllowedHosts.Contains(uri.Host, StringComparer.OrdinalIgnoreCase);

    /// <summary>Fetch the installer to a temp file, reporting progress 0..1.</summary>
    public async Task<string> DownloadAsync(UpdateInfo update, IProgress<double> progress,
                                            CancellationToken cancel = default)
    {
        if (!IsTrusted(update.DownloadUrl))
            throw new InvalidOperationException("refusing to download from an untrusted host");

        var folder = Path.Combine(Path.GetTempPath(), "GPO Fishing Macro Update");
        Directory.CreateDirectory(folder);
        var target = Path.Combine(folder, $"GPO-Fishing-Macro-Setup-{update.Tag}.exe");

        using var response = await Http.GetAsync(update.DownloadUrl,
            HttpCompletionOption.ResponseHeadersRead, cancel).ConfigureAwait(false);
        response.EnsureSuccessStatusCode();

        var total = response.Content.Headers.ContentLength ?? update.Size;
        await using var source = await response.Content.ReadAsStreamAsync(cancel)
                                               .ConfigureAwait(false);
        await using (var file = File.Create(target))
        {
            var buffer = new byte[81920];
            long written = 0;
            int read;
            while ((read = await source.ReadAsync(buffer, cancel).ConfigureAwait(false)) > 0)
            {
                await file.WriteAsync(buffer.AsMemory(0, read), cancel).ConfigureAwait(false);
                written += read;
                if (total > 0) progress.Report(Math.Min(1.0, (double)written / total));
            }
        }

        var actual = new FileInfo(target).Length;
        if (update.Size > 0 && actual != update.Size)
        {
            File.Delete(target);
            throw new IOException(
                $"the download is {actual} bytes but GitHub said {update.Size}");
        }
        return target;
    }

    /// <summary>Start the downloaded installer, which needs this copy closed:
    /// it holds the single-instance mutex and its own files are being replaced.</summary>
    public static void LaunchAndExit(string installerPath)
    {
        Process.Start(new ProcessStartInfo(installerPath) { UseShellExecute = true });
        System.Windows.Application.Current.Shutdown();
    }
}
