using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Globalization;
using System.Text.Json;
using System.Windows.Media;

namespace GpoMacro;

/// <summary>
/// Everything the window shows. The engine pushes ticks and events in; this
/// turns them into the words and colours DESIGN.md asks for.
///
/// Colour is never the only signal: every coloured thing here has a word
/// beside it, so the app reads the same in greyscale.
/// </summary>
public sealed class MainViewModel : Observable
{
    private const int LogLimit = 300;

    private Dictionary<string, string> _stateColours = new();
    private Dictionary<string, (string Mark, string Colour)> _eventStyles = new();

    public ObservableCollection<LogEntry> Log { get; } = new();
    public ObservableCollection<SettingsSection> Sections { get; } = new();

    // ---------------------------------------------------------- status strip

    private string _stateWord = "IDLE";
    public string StateWord { get => _stateWord; set => Set(ref _stateWord, value); }

    private Brush _stateBrush = Brushes.Gray;
    public Brush StateBrush { get => _stateBrush; set => Set(ref _stateBrush, value); }

    private string _windowLine = "looking for Roblox...";
    public string WindowLine { get => _windowLine; set => Set(ref _windowLine, value); }

    private string _hint = "";
    public string Hint { get => _hint; set => Set(ref _hint, value); }

    private Brush _hintBrush = Brushes.Gray;
    public Brush HintBrush { get => _hintBrush; set => Set(ref _hintBrush, value); }

    private string _startLabel = "Start";
    public string StartLabel { get => _startLabel; set => Set(ref _startLabel, value); }

    private Brush _startBrush = Brushes.Green;
    public Brush StartBrush { get => _startBrush; set => Set(ref _startBrush, value); }

    private bool _canPause;
    public bool CanPause { get => _canPause; set => Set(ref _canPause, value); }

    // --------------------------------------------------------------- counters

    private string _caught = "-";
    public string Caught { get => _caught; set => Set(ref _caught, value); }

    private string _perHour = "-";
    public string PerHour { get => _perHour; set => Set(ref _perHour, value); }

    private string _hookRate = "-";
    public string HookRate { get => _hookRate; set => Set(ref _hookRate, value); }

    private string _escaped = "-";
    public string Escaped { get => _escaped; set => Set(ref _escaped, value); }

    private string _timeouts = "-";
    public string Timeouts { get => _timeouts; set => Set(ref _timeouts, value); }

    private string _session = "-";
    public string Session { get => _session; set => Set(ref _session, value); }

    // ------------------------------------------------------------ reel readout

    private string _holdWord = "-";
    public string HoldWord { get => _holdWord; set => Set(ref _holdWord, value); }

    private Brush _holdBrush = Brushes.Gray;
    public Brush HoldBrush { get => _holdBrush; set => Set(ref _holdBrush, value); }

    private string _onFishLine = "";
    public string OnFishLine { get => _onFishLine; set => Set(ref _onFishLine, value); }

    private Brush _onFishBrush = Brushes.Gray;
    public Brush OnFishBrush { get => _onFishBrush; set => Set(ref _onFishBrush, value); }

    private double _meter;
    public double Meter { get => _meter; set => Set(ref _meter, value); }

    private Brush _meterBrush = Brushes.Green;
    public Brush MeterBrush { get => _meterBrush; set => Set(ref _meterBrush, value); }

    private string _telemetryLine = "start the macro to see live readings";
    public string TelemetryLine { get => _telemetryLine; set => Set(ref _telemetryLine, value); }

    private Brush _telemetryBrush = Brushes.Gray;
    public Brush TelemetryBrush { get => _telemetryBrush; set => Set(ref _telemetryBrush, value); }

    private ImageSource? _preview;
    public ImageSource? Preview { get => _preview; set => Set(ref _preview, value); }

    private string _previewNote = "";
    public string PreviewNote { get => _previewNote; set => Set(ref _previewNote, value); }

    // --------------------------------------------------------------- settings

    private string _dirtyLine = "";
    public string DirtyLine { get => _dirtyLine; set => Set(ref _dirtyLine, value); }

    private Brush _saveBrush = Brushes.Green;
    public Brush SaveBrush { get => _saveBrush; set => Set(ref _saveBrush, value); }

    private string _webhookLine = "";
    public string WebhookLine { get => _webhookLine; set => Set(ref _webhookLine, value); }

    private Brush _webhookBrush = Brushes.Gray;
    public Brush WebhookBrush { get => _webhookBrush; set => Set(ref _webhookBrush, value); }

    // ---------------------------------------------------------------- updates

    public string VersionLine => $"version {Updater.CurrentVersionText()}";

    private string _updateStatus = "";
    public string UpdateStatus { get => _updateStatus; set => Set(ref _updateStatus, value); }

    private Brush _updateBrush = Brushes.Gray;
    public Brush UpdateBrush { get => _updateBrush; set => Set(ref _updateBrush, value); }

    private bool _updateAvailable;
    public bool UpdateAvailable { get => _updateAvailable; set => Set(ref _updateAvailable, value); }

    private bool _updateBusy;
    public bool UpdateBusy
    {
        get => _updateBusy;
        set { if (Set(ref _updateBusy, value)) Raise(nameof(CanCheckUpdates)); }
    }

    public bool CanCheckUpdates => !_updateBusy;

    private double _updateProgress;
    public double UpdateProgress { get => _updateProgress; set => Set(ref _updateProgress, value); }

    private bool _updateProgressVisible;
    public bool UpdateProgressVisible
    {
        get => _updateProgressVisible;
        set => Set(ref _updateProgressVisible, value);
    }

    private string _updateButtonText = "Download and install";
    public string UpdateButtonText
    {
        get => _updateButtonText;
        set => Set(ref _updateButtonText, value);
    }

    // ------------------------------------------------------------ calibration

    private string _regionLine = "not set";
    public string RegionLine { get => _regionLine; set => Set(ref _regionLine, value); }

    private Brush _regionBrush = Brushes.Gray;
    public Brush RegionBrush { get => _regionBrush; set => Set(ref _regionBrush, value); }

    private string _colourLines = "";
    public string ColourLines { get => _colourLines; set => Set(ref _colourLines, value); }

    private string _fruitLines = "";
    private string _baitLines = "";
    public string FruitLines { get => _fruitLines; set => Set(ref _fruitLines, value); }
    public string BaitLines { get => _baitLines; set => Set(ref _baitLines, value); }

    // ----------------------------------------------------------------- intake

    public void AdoptStyles(JsonElement hello)
    {
        if (hello.TryGetProperty("state_colors", out var states))
        {
            _stateColours = new Dictionary<string, string>();
            foreach (var entry in states.EnumerateObject())
                _stateColours[entry.Name] = entry.Value.GetString() ?? "";
        }
        if (!hello.TryGetProperty("event_styles", out var events)) return;
        _eventStyles = new Dictionary<string, (string, string)>();
        foreach (var entry in events.EnumerateObject())
            _eventStyles[entry.Name] = (
                entry.Value.GetProperty("mark").GetString() ?? "-",
                entry.Value.GetProperty("colour").GetString() ?? "");
    }

    public void AddLog(string kind, string message)
    {
        var mark = "-";
        string? colour = null;
        if (_eventStyles.TryGetValue(kind, out var style)) (mark, colour) = style;
        Log.Add(new LogEntry(mark, message, Palette.FromHex(colour, "Text2")));
        while (Log.Count > LogLimit) Log.RemoveAt(0);
    }

    /// <summary>One 10 Hz frame from the engine.</summary>
    public void ApplyTick(JsonElement tick, string toggleKey, bool livePreview)
    {
        var running = tick.GetProperty("running").GetBoolean();
        var state = tick.GetProperty("state").GetString() ?? "idle";
        StateWord = state.ToUpperInvariant();
        StateBrush = Palette.FromHex(_stateColours.GetValueOrDefault(state), "Faint");

        var hasWindow = tick.TryGetProperty("window", out var window)
                        && window.ValueKind == JsonValueKind.Object;
        WindowLine = hasWindow
            ? $"\"{window.GetProperty("title").GetString()}\"  " +
              $"{window.GetProperty("width").GetInt32()}x{window.GetProperty("height").GetInt32()}" +
              $"  at ({window.GetProperty("left").GetInt32()},{window.GetProperty("top").GetInt32()})"
            : "no Roblox window";

        // The hint is the empty state: it says what to do, never just "idle".
        var regionValid = tick.GetProperty("region_valid").GetBoolean();
        (Hint, HintBrush) = (hasWindow, regionValid, running, state) switch
        {
            (false, _, _, _) => ("Launch Roblox and open GPO.", Palette.Named("Amber")),
            (_, false, _, _) => ("Calibrate the scan region before starting.", Palette.Named("Amber")),
            (_, _, false, _) => ($"Ready. Start here or press {toggleKey}.", Palette.Named("Muted")),
            (_, _, _, "paused") => ($"Paused. Press {toggleKey} to resume.", Palette.Named("Amber")),
            (_, _, _, "wait") => ("Line is out, waiting for a bite.", Palette.Named("Muted")),
            (_, _, _, "reel") => ("Fighting a fish.", Palette.Named("Green")),
            _ => (state, Palette.Named("Muted")),
        };

        StartLabel = running ? "Stop" : "Start";
        StartBrush = Palette.Named(running ? "Amber" : "Green");
        CanPause = running;

        var stats = tick.GetProperty("stats");
        var caught = stats.GetProperty("fish_total").GetInt32();
        var escaped = stats.GetProperty("fails").GetInt32();
        var landed = caught + escaped;
        Caught = caught.ToString(CultureInfo.InvariantCulture);
        PerHour = stats.GetProperty("fish_per_hour").GetDouble()
                       .ToString("F0", CultureInfo.InvariantCulture);
        HookRate = landed > 0
            ? (100.0 * caught / landed).ToString("F0", CultureInfo.InvariantCulture) + "%"
            : "-";
        Escaped = escaped.ToString(CultureInfo.InvariantCulture);
        Timeouts = stats.GetProperty("recast_timeouts").GetInt32()
                        .ToString(CultureInfo.InvariantCulture);
        Session = FormatUptime(stats.GetProperty("uptime_seconds").GetDouble());

        ApplyTelemetry(tick, running);
        ApplyWebhook(tick);
        PreviewNote = livePreview ? "" : "preview off";
        if (!livePreview) Preview = null;
    }

    private void ApplyTelemetry(JsonElement tick, bool running)
    {
        var hasTelemetry = tick.TryGetProperty("telemetry", out var telemetry)
                           && telemetry.ValueKind == JsonValueKind.Object;
        if (!hasTelemetry)
        {
            HoldWord = "-";
            HoldBrush = Palette.Named("Faint");
            OnFishLine = "";
            OnFishBrush = Palette.Named("Faint");
            Meter = 0;
            MeterBrush = Palette.Named("GreenDim");
            TelemetryLine = running
                ? "waiting for a bite, readings appear here during a fight"
                : "start the macro to see live readings";
            TelemetryBrush = Palette.Named("Faint");
            return;
        }

        var holding = telemetry.GetProperty("hold").GetBoolean();
        var onFish = telemetry.GetProperty("on_bar").GetBoolean();
        var percent = telemetry.GetProperty("on_bar_pct").GetDouble();

        HoldWord = holding ? "HOLD" : "drop";
        HoldBrush = Palette.Named(holding ? "Green" : "Muted");
        OnFishLine = (onFish ? "on the fish" : "off the fish") +
                     $"   {percent.ToString("F0", CultureInfo.InvariantCulture)}% this fight";
        OnFishBrush = Palette.Named(onFish ? "Green" : "Muted");
        Meter = Math.Clamp(percent / 100.0, 0.0, 1.0);
        MeterBrush = Palette.Named(onFish ? "Green" : "GreenDim");

        // Signed velocities and error, so a glance says which way things move.
        TelemetryLine = string.Format(CultureInfo.InvariantCulture,
            "bar {0,5:F0} {1,5:+0;-0}/s    fish {2,5:F0} {3,5:+0;-0}/s    " +
            "error {4,5:+0;-0}    {5,4:F1}s {6,4} frames",
            telemetry.GetProperty("bar_y").GetDouble(),
            telemetry.GetProperty("bar_vel").GetDouble(),
            telemetry.GetProperty("fish_y").GetDouble(),
            telemetry.GetProperty("fish_vel").GetDouble(),
            telemetry.GetProperty("error").GetDouble(),
            telemetry.GetProperty("elapsed").GetDouble(),
            telemetry.GetProperty("frames").GetInt32());
        TelemetryBrush = Palette.Named("Text");
    }

    private void ApplyWebhook(JsonElement tick)
    {
        if (!tick.TryGetProperty("webhook", out var hook)) return;
        if (!hook.GetProperty("configured").GetBoolean())
        {
            WebhookLine = hook.GetProperty("last_result").GetString() ?? "";
            WebhookBrush = Palette.Named("Faint");
            return;
        }
        var sent = hook.GetProperty("sent").GetInt32();
        var failed = hook.GetProperty("failed").GetInt32();
        var parts = new List<string> { hook.GetProperty("last_result").GetString() ?? "" };
        if (sent > 0 || failed > 0) parts.Add($"{sent} sent, {failed} failed");
        var queued = hook.GetProperty("queued").GetInt32();
        if (queued > 0) parts.Add($"{queued} queued");
        var dropped = hook.GetProperty("dropped").GetInt32();
        if (dropped > 0) parts.Add($"{dropped} dropped");
        var suppressed = hook.GetProperty("suppressed").GetInt32();
        if (suppressed > 0) parts.Add($"{suppressed} throttled");
        WebhookLine = string.Join("   ", parts);
        WebhookBrush = Palette.Named(sent > 0 && failed == 0 ? "Green" : failed > 0 ? "Amber" : "Faint");
    }

    public void RefreshDirty()
    {
        var count = 0;
        foreach (var section in Sections)
            foreach (var field in section.Fields)
                if (field.IsDirty) count++;
        DirtyLine = count == 0 ? "" : $"{count} unsaved change{(count == 1 ? "" : "s")}";
        SaveBrush = Palette.Named(count == 0 ? "Green" : "Amber");
    }

    public static string FormatUptime(double seconds)
    {
        var total = (int)Math.Max(0, seconds);
        return $"{total / 3600:D}:{total / 60 % 60:D2}:{total % 60:D2}";
    }
}
