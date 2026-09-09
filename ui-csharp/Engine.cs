using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;

namespace GpoMacro;

/// <summary>
/// The Python engine as seen from the shell: one child process speaking
/// newline-delimited JSON on stdin/stdout (see gpo_macro/rpc.py).
///
/// Every screen grab, mouse press and OpenCV call still happens over there.
/// This class owns nothing but the pipe.
/// </summary>
public sealed class Engine : IDisposable
{
    private readonly ConcurrentDictionary<int, TaskCompletionSource<JsonElement>> _pending = new();
    private Process? _process;
    private readonly SemaphoreSlim _writeLock = new(1, 1);
    private int _nextId;
    private volatile bool _stopping;

    /// <summary>A protocol frame arrived. Always raised on the UI thread.</summary>
    public event Action<JsonElement>? Frame;

    /// <summary>The engine died or wrote something unparseable. UI thread.</summary>
    public event Action<string>? Fault;

    public string RepoRoot { get; private set; } = "";
    public string PythonPath { get; private set; } = "";
    public bool IsRunning => _process is { HasExited: false };

    // ------------------------------------------------------------------ start

    public void Start()
    {
        RepoRoot = FindRepoRoot();
        PythonPath = FindPython(RepoRoot);

        var info = new ProcessStartInfo
        {
            FileName = PythonPath,
            WorkingDirectory = RepoRoot,
            RedirectStandardInput = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            CreateNoWindow = true,
            StandardOutputEncoding = new UTF8Encoding(false),
            StandardErrorEncoding = new UTF8Encoding(false),
        };
        info.ArgumentList.Add("main.py");
        info.ArgumentList.Add("--rpc");
        // Unbuffered, so a tick is not stuck in a pipe buffer for a second.
        info.Environment["PYTHONUNBUFFERED"] = "1";
        info.Environment["PYTHONIOENCODING"] = "utf-8";

        _process = Process.Start(info)
                   ?? throw new InvalidOperationException($"could not start {PythonPath}");

        _ = Task.Run(ReadLoop);
        _ = Task.Run(ReadErrorLoop);
    }

    private async Task ReadLoop()
    {
        var reader = _process!.StandardOutput;
        while (true)
        {
            string? line;
            try { line = await reader.ReadLineAsync().ConfigureAwait(false); }
            catch (Exception ex) { Post(() => Fault?.Invoke($"engine read failed: {ex.Message}")); return; }

            if (line is null)
            {
                if (!_stopping)
                    Post(() => Fault?.Invoke("engine exited - see gpo_macro.log / crash.log"));
                FailAllPending("engine exited");
                return;
            }
            if (line.Length == 0) continue;

            JsonElement frame;
            try
            {
                using var doc = JsonDocument.Parse(line);
                frame = doc.RootElement.Clone();
            }
            catch (JsonException)
            {
                // A traceback or a stray print. Surface it rather than dropping it.
                var text = line.Length > 400 ? line[..400] + "..." : line;
                Post(() => Fault?.Invoke(text));
                continue;
            }

            if (frame.TryGetProperty("t", out var kind) && kind.GetString() == "reply"
                && frame.TryGetProperty("id", out var idElement)
                && idElement.ValueKind == JsonValueKind.Number
                && _pending.TryRemove(idElement.GetInt32(), out var waiter))
            {
                waiter.TrySetResult(frame);
                continue;
            }
            Post(() => Frame?.Invoke(frame));
        }
    }

    private async Task ReadErrorLoop()
    {
        var reader = _process!.StandardError;
        while (await reader.ReadLineAsync().ConfigureAwait(false) is { } line)
        {
            if (line.Length == 0) continue;
            var text = line;
            Post(() => Fault?.Invoke(text));
        }
    }

    // --------------------------------------------------------------- commands

    /// <summary>Fire a command and await its reply. Never throws on engine
    /// error - inspect <c>ok</c> on the returned frame.</summary>
    public async Task<JsonElement> CallAsync(string command, object? args = null,
                                             int timeoutSeconds = 30)
    {
        if (_process is null || _process.HasExited)
            return Synthetic(false, "engine is not running");

        var id = Interlocked.Increment(ref _nextId);
        var payload = new Dictionary<string, object?> { ["id"] = id, ["cmd"] = command };
        if (args is not null)
            foreach (var (key, value) in ToDictionary(args))
                payload[key] = value;

        var waiter = new TaskCompletionSource<JsonElement>(
            TaskCreationOptions.RunContinuationsAsynchronously);
        _pending[id] = waiter;

        await _writeLock.WaitAsync().ConfigureAwait(false);
        try
        {
            var line = JsonSerializer.Serialize(payload);
            await _process.StandardInput.WriteLineAsync(line).ConfigureAwait(false);
            await _process.StandardInput.FlushAsync().ConfigureAwait(false);
        }
        catch (Exception ex)
        {
            _pending.TryRemove(id, out _);
            return Synthetic(false, $"could not reach the engine: {ex.Message}");
        }
        finally { _writeLock.Release(); }

        var finished = await Task.WhenAny(
            waiter.Task, Task.Delay(TimeSpan.FromSeconds(timeoutSeconds))).ConfigureAwait(false);
        if (finished != waiter.Task)
        {
            _pending.TryRemove(id, out _);
            return Synthetic(false, $"'{command}' timed out after {timeoutSeconds}s");
        }
        return await waiter.Task.ConfigureAwait(false);
    }

    /// <summary>Fire and forget, for controls that must not block the UI.</summary>
    public void Send(string command, object? args = null) => _ = CallAsync(command, args);

    private static IEnumerable<KeyValuePair<string, object?>> ToDictionary(object args)
    {
        if (args is IDictionary<string, object?> map) return map;
        var json = JsonSerializer.Serialize(args);
        return JsonSerializer.Deserialize<Dictionary<string, object?>>(json)
               ?? new Dictionary<string, object?>();
    }

    private static JsonElement Synthetic(bool ok, string error)
    {
        var json = JsonSerializer.Serialize(new { t = "reply", ok, error });
        using var doc = JsonDocument.Parse(json);
        return doc.RootElement.Clone();
    }

    private void FailAllPending(string reason)
    {
        foreach (var key in _pending.Keys)
            if (_pending.TryRemove(key, out var waiter))
                waiter.TrySetResult(Synthetic(false, reason));
    }

    private static void Post(Action action)
    {
        var app = Application.Current;
        if (app is null) return;
        app.Dispatcher.BeginInvoke(action);
    }

    // ---------------------------------------------------------------- locating

    /// <summary>Walk up from the executable until main.py turns up, so the app
    /// runs from bin/Debug and from a published folder alike.</summary>
    private static string FindRepoRoot()
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        for (var i = 0; i < 8 && dir is not null; i++, dir = dir.Parent)
            if (File.Exists(Path.Combine(dir.FullName, "main.py")))
                return dir.FullName;

        var cwd = Directory.GetCurrentDirectory();
        if (File.Exists(Path.Combine(cwd, "main.py"))) return cwd;
        throw new FileNotFoundException(
            "main.py not found above " + AppContext.BaseDirectory +
            " - run MacroUI.exe from inside the macro folder.");
    }

    /// <summary>An interpreter that actually has mss, OpenCV and pynput.
    ///
    /// An installed copy ships its own Python under runtime/, so nothing on the
    /// machine needs to be set up and nothing on PATH can shadow it. A source
    /// checkout uses the project venv. PATH is the last resort and usually the
    /// wrong answer, which is why a missing import is reported plainly rather
    /// than left to fail deep inside an import trace.</summary>
    private static string FindPython(string root)
    {
        foreach (var candidate in new[]
                 {
                     Path.Combine(root, "runtime", "python.exe"),   // installed
                     Path.Combine(root, ".venv", "Scripts", "python.exe"),
                     Path.Combine(root, "venv", "Scripts", "python.exe"),
                 })
            if (File.Exists(candidate)) return candidate;
        return "python";   // fall back to PATH
    }

    // ----------------------------------------------------------------- dispose

    public void Dispose()
    {
        _stopping = true;
        try
        {
            if (_process is { HasExited: false })
            {
                _process.StandardInput.WriteLine("{\"cmd\":\"shutdown\"}");
                _process.StandardInput.Flush();
                if (!_process.WaitForExit(4000)) _process.Kill(entireProcessTree: true);
            }
        }
        catch
        {
            try { _process?.Kill(entireProcessTree: true); } catch { /* already gone */ }
        }
        _process?.Dispose();
        _process = null;
    }
}
