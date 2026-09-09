using System;
using System.Collections.Generic;
using System.Collections.Specialized;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Interop;
using System.Windows.Threading;
using System.Windows.Media.Imaging;

namespace GpoMacro;

public partial class MainWindow : Window
{
    private static readonly string[] BaitShopPoints =
        { "open bait shop", "buy common bait", "confirm (if any)", "close shop" };

    private static readonly string[] BaitCraftPoints =
        { "open crafting menu", "select recipe", "select amount (optional)",
          "craft button", "confirm (if any)", "close menu" };

    private readonly MainViewModel _model = new();
    private readonly Engine _engine = new();
    private readonly Updater _updater = new();
    private UpdateInfo? _pendingUpdate;

    private JsonElement _config;
    private bool _haveConfig;
    private string _toggleKey = "f6";
    private bool _livePreview;
    private bool _closing;
    private bool _scrollQueued;
    private bool _checkedForUpdates;

    public MainWindow()
    {
        InitializeComponent();
        DataContext = _model;

        _model.Log.CollectionChanged += ScrollLogToEnd;
        _engine.Frame += OnFrame;
        _engine.Fault += OnFault;

        SourceInitialized += (_, _) =>
            Native.UseDarkTitleBar(new WindowInteropHelper(this).Handle);
        Loaded += OnLoaded;
        Closing += OnClosing;

#if DEBUG
        // Ctrl+Shift+L floods the log the way a burst of engine events does.
        // This is how the "ItemsControl is inconsistent with its items source"
        // crash is reproduced; it is not compiled into a release build.
        KeyDown += (_, args) =>
        {
            if (args.Key != System.Windows.Input.Key.L
                || Keyboard.Modifiers != (ModifierKeys.Control | ModifierKeys.Shift)) return;
            for (var i = 0; i < 400; i++)
            {
                var n = i;
                Dispatcher.BeginInvoke(new Action(
                    () => _model.AddLog(n % 3 == 0 ? "fish" : "info", $"flood line {n}")));
            }
        };
#endif
    }

    // ------------------------------------------------------------- lifecycle

    private void OnLoaded(object sender, RoutedEventArgs e)
    {
        try
        {
            _engine.Start();
        }
        catch (Exception ex)
        {
            _model.AddLog("error", ex.Message);
            MessageBox.Show(
                ex.Message + "\n\nThe interface needs the macro's Python engine. " +
                "Run it from the folder that holds main.py, with the .venv set up.",
                "GPO Fishing Macro", MessageBoxButton.OK, MessageBoxImage.Error);
        }
    }

    private void OnClosing(object? sender, System.ComponentModel.CancelEventArgs e)
    {
        // Stopping the engine releases the mouse button, so this must happen
        // even if the window is being closed in a hurry.
        _closing = true;
        _engine.Dispose();
    }

    /// <summary>Follow the tail of the log.
    ///
    /// Never scroll from inside the CollectionChanged handler. Doing so makes
    /// the ListBox schedule a viewport pass that calls UpdateLayout while more
    /// lines are still arriving, and the item generator ends up disagreeing
    /// with the collection it is generating from ("An ItemsControl is
    /// inconsistent with its items source"). The scroll is deferred below
    /// layout priority instead, and coalesced, so a burst of twenty events
    /// costs one scroll after the list has settled.</summary>
    private void ScrollLogToEnd(object? sender, NotifyCollectionChangedEventArgs e)
    {
        if (e.Action != NotifyCollectionChangedAction.Add || _scrollQueued) return;
        _scrollQueued = true;
        Dispatcher.BeginInvoke(DispatcherPriority.Background, new Action(() =>
        {
            _scrollQueued = false;
            if (_model.Log.Count > 0) LogList.ScrollIntoView(_model.Log[^1]);
        }));
    }

    // ---------------------------------------------------------------- frames

    private void OnFrame(JsonElement frame)
    {
        if (!frame.TryGetProperty("t", out var kindElement)) return;
        switch (kindElement.GetString())
        {
            case "hello":
                _model.AdoptStyles(frame);
                BuildSettings(frame);
                AdoptConfig(frame.GetProperty("config"));
                _model.AddLog("info",
                    $"Welcome - calibrate once (Calibration tab), then Start. " +
                    $"Hotkeys: {_toggleKey} toggle, {HotkeyValue("panic")} panic.");
                break;

            case "event":
                _model.AddLog(frame.GetProperty("kind").GetString() ?? "info",
                              frame.GetProperty("message").GetString() ?? "");
                break;

            case "tick":
                _model.ApplyTick(frame, _toggleKey, _livePreview);
                if (frame.TryGetProperty("preview", out var preview))
                    _model.Preview = DecodePng(preview.GetString());
                break;
        }
    }

    private void OnFault(string text)
    {
        if (_closing) return;
        _model.AddLog("error", text);
    }

    private static BitmapImage? DecodePng(string? base64)
    {
        if (string.IsNullOrEmpty(base64)) return null;
        try
        {
            var image = new BitmapImage();
            image.BeginInit();
            image.StreamSource = new MemoryStream(Convert.FromBase64String(base64));
            image.CacheOption = BitmapCacheOption.OnLoad;
            image.EndInit();
            image.Freeze();
            return image;
        }
        catch (Exception)
        {
            return null;   // a torn frame is not worth a fault line
        }
    }

    // ---------------------------------------------------------------- config

    /// <summary>Build the settings form from the engine's schema, so a knob
    /// added in form.py appears here with no C# change.</summary>
    private void BuildSettings(JsonElement hello)
    {
        var notes = hello.GetProperty("notes");
        _model.Sections.Clear();
        var byName = new Dictionary<string, SettingsSection>();

        foreach (var entry in hello.GetProperty("schema").EnumerateArray())
        {
            var name = entry.GetProperty("section").GetString() ?? "";
            if (!byName.TryGetValue(name, out var section))
            {
                var note = notes.TryGetProperty(name, out var noteElement)
                    ? noteElement.GetString() ?? "" : "";
                section = new SettingsSection(name, note);
                byName[name] = section;
                _model.Sections.Add(section);
            }
            var field = new SettingField(
                entry.GetProperty("obj").GetString() ?? "",
                entry.GetProperty("attr").GetString() ?? "",
                entry.GetProperty("label").GetString() ?? "",
                entry.GetProperty("kind").GetString() ?? "str");
            field.PropertyChanged += (_, args) =>
            {
                if (args.PropertyName == nameof(SettingField.IsDirty)) _model.RefreshDirty();
            };
            section.Fields.Add(field);
        }
    }

    private void AdoptConfig(JsonElement config)
    {
        _config = config.Clone();
        _haveConfig = true;

        foreach (var section in _model.Sections)
            foreach (var field in section.Fields)
                if (_config.TryGetProperty(field.Object, out var group)
                    && group.TryGetProperty(field.Attr, out var value))
                    field.Load(value);

        _toggleKey = HotkeyValue("start_stop");
        var ui = _config.GetProperty("ui");
        _livePreview = ui.GetProperty("live_preview").GetBoolean();
        Topmost = ui.GetProperty("always_on_top").GetBoolean();
        _model.RefreshDirty();
        RefreshCalibrationReadouts();

        // Once per launch, and only if the setting allows it.
        if (!_checkedForUpdates
            && ui.TryGetProperty("check_for_updates", out var wanted) && wanted.GetBoolean())
        {
            _checkedForUpdates = true;
            _ = CheckUpdatesAsync(announce: false);
        }
    }

    private string HotkeyValue(string attr) =>
        _haveConfig && _config.TryGetProperty("hotkeys", out var hotkeys)
        && hotkeys.TryGetProperty(attr, out var value)
            ? value.GetString() ?? "" : "";

    private async Task ReloadConfigAsync()
    {
        var reply = await _engine.CallAsync("get_config");
        if (Ok(reply, out var result)) AdoptConfig(result.GetProperty("config"));
    }

    private static bool Ok(JsonElement reply, out JsonElement result)
    {
        result = default;
        if (reply.TryGetProperty("ok", out var ok) && ok.GetBoolean()
            && reply.TryGetProperty("result", out var value))
        {
            result = value;
            return true;
        }
        return false;
    }

    private void ReportFailure(JsonElement reply, string what)
    {
        var detail = reply.TryGetProperty("error", out var error)
            ? error.GetString() : "no reason given";
        _model.AddLog("error", $"{what}: {detail}");
    }

    // ------------------------------------------------------- dashboard actions

    private void Start_Click(object sender, RoutedEventArgs e) => _ = ToggleAsync();

    private async Task ToggleAsync()
    {
        var reply = await _engine.CallAsync("toggle");
        if (!Ok(reply, out var result)) { ReportFailure(reply, "start/stop"); return; }
        if (result.TryGetProperty("reason", out var reason))
            _model.AddLog("warn", $"cannot start - {reason.GetString()}");
    }

    private void Pause_Click(object sender, RoutedEventArgs e) => _engine.Send("pause");

    private void Panic_Click(object sender, RoutedEventArgs e) => _engine.Send("panic");

    // -------------------------------------------------------- settings actions

    private void Secret_Loaded(object sender, RoutedEventArgs e)
    {
        if (sender is PasswordBox box && box.DataContext is SettingField field)
            box.Password = field.Text;
    }

    private void Secret_Changed(object sender, RoutedEventArgs e)
    {
        if (sender is PasswordBox box && box.DataContext is SettingField field)
            field.Text = box.Password;
    }

    private void Save_Click(object sender, RoutedEventArgs e) => _ = SaveAsync();

    private async Task<bool> SaveAsync(bool quiet = false)
    {
        var patch = new Dictionary<string, object?>();
        foreach (var section in _model.Sections)
            foreach (var field in section.Fields)
            {
                object? value;
                try
                {
                    value = field.Value();
                }
                catch (FormatException)
                {
                    _model.AddLog("error",
                        $"invalid value for \"{field.Label}\": {field.Text}");
                    return false;
                }
                catch (OverflowException)
                {
                    _model.AddLog("error", $"value out of range for \"{field.Label}\"");
                    return false;
                }
                if (!patch.TryGetValue(field.Object, out var group))
                    patch[field.Object] = group = new Dictionary<string, object?>();
                ((Dictionary<string, object?>)group!)[field.Attr] = value;
            }

        var reply = await _engine.CallAsync("set_config",
            new Dictionary<string, object?> { ["patch"] = patch });
        if (!Ok(reply, out var result)) { ReportFailure(reply, "save"); return false; }
        AdoptConfig(result.GetProperty("config"));
        if (!quiet) _model.AddLog("info", "settings saved");
        return true;
    }

    private void ResetStats_Click(object sender, RoutedEventArgs e)
    {
        _engine.Send("reset_stats");
        _model.AddLog("info", "session stats reset");
    }

    private void WebhookTest_Click(object sender, RoutedEventArgs e) => _ = WebhookTestAsync();

    private async Task WebhookTestAsync()
    {
        // Save first: otherwise this tests the URL from before the paste.
        if (!await SaveAsync(quiet: true)) return;
        var reply = await _engine.CallAsync("webhook_test");
        if (!Ok(reply, out var result)) { ReportFailure(reply, "webhook test"); return; }
        var problem = result.GetProperty("problem").GetString();
        _model.AddLog(string.IsNullOrEmpty(problem) ? "info" : "warn",
            string.IsNullOrEmpty(problem)
                ? "webhook: test message queued"
                : $"webhook: {problem}");
    }

    // --------------------------------------------------------- update actions

    private void CheckUpdates_Click(object sender, RoutedEventArgs e) =>
        _ = CheckUpdatesAsync(announce: true);

    /// <summary>Ask GitHub what the latest release is.
    ///
    /// On launch this runs quietly: someone opening the app to go fishing does
    /// not want a dialog, and a network hiccup is not worth a red log line. The
    /// button reports everything.</summary>
    private async Task CheckUpdatesAsync(bool announce)
    {
        _model.UpdateBusy = true;
        _model.UpdateStatus = "checking...";
        _model.UpdateBrush = Palette.Named("Faint");
        try
        {
            var (update, status) = await _updater.CheckAsync();
            _pendingUpdate = update;
            _model.UpdateAvailable = update is not null;
            _model.UpdateStatus = status;
            _model.UpdateBrush = Palette.Named(update is not null ? "Green" : "Faint");
            if (update is not null)
                _model.UpdateButtonText =
                    $"Download and install {update.Tag} ({update.Size / 1_000_000} MB)";
            if (update is not null || announce)
                _model.AddLog(update is not null ? "milestone" : "info", $"update check: {status}");
        }
        finally
        {
            _model.UpdateBusy = false;
        }
    }

    private void InstallUpdate_Click(object sender, RoutedEventArgs e) => _ = InstallUpdateAsync();

    private async Task InstallUpdateAsync()
    {
        if (_pendingUpdate is null) return;

        // The macro drives the mouse. Replacing the program underneath a
        // running bot is a bad idea, so stop it first and say so.
        var confirm = MessageBox.Show(
            $"Download {_pendingUpdate.Tag} ({_pendingUpdate.Size / 1_000_000} MB) and install it?\n\n" +
            "The macro will stop and this window will close while the installer runs. " +
            "Your settings and calibration are kept.",
            "GPO Fishing Macro", MessageBoxButton.OKCancel, MessageBoxImage.Question);
        if (confirm != MessageBoxResult.OK) return;

        _engine.Send("stop");
        _model.UpdateBusy = true;
        _model.UpdateProgressVisible = true;
        _model.UpdateProgress = 0;
        _model.UpdateStatus = "downloading...";
        _model.UpdateBrush = Palette.Named("Amber");
        try
        {
            var progress = new Progress<double>(fraction =>
            {
                _model.UpdateProgress = fraction;
                _model.UpdateStatus = $"downloading... {fraction * 100:F0}%";
            });
            var installer = await _updater.DownloadAsync(_pendingUpdate, progress);
            _model.UpdateStatus = "starting the installer";
            _model.AddLog("info", $"downloaded {_pendingUpdate.Tag}, handing over to the installer");
            _engine.Dispose();          // release the mouse before we go
            Updater.LaunchAndExit(installer);
        }
        catch (Exception ex)
        {
            _model.UpdateProgressVisible = false;
            _model.UpdateStatus = "download failed";
            _model.UpdateBrush = Palette.Named("Red");
            _model.AddLog("error", $"update failed: {ex.Message}");
        }
        finally
        {
            _model.UpdateBusy = false;
        }
    }

    // ----------------------------------------------------- calibration actions

    /// <summary>Screen pixels to Roblox-window-relative, using the origin the
    /// engine reports - the engine decides which window "Roblox" means.</summary>
    private async Task<(int X, int Y)?> ClientOriginAsync()
    {
        var reply = await _engine.CallAsync("window");
        if (!Ok(reply, out var result)) { ReportFailure(reply, "find Roblox"); return null; }
        if (!result.GetProperty("found").GetBoolean())
        {
            _model.AddLog("warn", "no Roblox window - launch GPO before calibrating");
            return null;
        }
        return (result.GetProperty("origin_x").GetInt32(),
                result.GetProperty("origin_y").GetInt32());
    }

    private void AutoDetect_Click(object sender, RoutedEventArgs e) => _ = AutoDetectAsync();

    private async Task AutoDetectAsync()
    {
        var reply = await _engine.CallAsync("auto_calibrate", timeoutSeconds: 60);
        if (!Ok(reply, out var result)) { ReportFailure(reply, "auto-detect"); return; }
        foreach (var note in result.GetProperty("notes").EnumerateArray())
            _model.AddLog("debug", "   " + note.GetString());
        var confident = result.GetProperty("confidence").GetDouble() >= 0.5;
        _model.AddLog(confident ? "info" : "warn",
                      result.GetProperty("message").GetString() ?? "");
        await ReloadConfigAsync();
    }

    private void DragRegion_Click(object sender, RoutedEventArgs e) => _ = DragRegionAsync();

    private async Task DragRegionAsync()
    {
        var region = await PickRegionAsync("Drag a box around the fishing gauge");
        if (region is null) return;
        var (x1, y1, x2, y2) = region.Value;
        await PatchAsync("scan_region",
            new Dictionary<string, object?> { ["x1"] = x1, ["y1"] = y1, ["x2"] = x2, ["y2"] = y2 });
        _model.AddLog("info", $"scan region set: ({x1},{y1}) - ({x2},{y2})");
    }

    /// <summary>Drag a box and translate it into window-relative pixels.</summary>
    private async Task<(int X1, int Y1, int X2, int Y2)?> PickRegionAsync(string instructions)
    {
        var origin = await ClientOriginAsync();
        if (origin is null) return null;
        var rect = OverlayWindow.DragRegion(this, instructions);
        if (rect is null) return null;
        var (ox, oy) = origin.Value;
        return (rect.Value.X1 - ox, rect.Value.Y1 - oy,
                rect.Value.X2 - ox, rect.Value.Y2 - oy);
    }

    private async Task PatchAsync(string objectName, object? value)
    {
        var reply = await _engine.CallAsync("set_config",
            new Dictionary<string, object?>
            {
                ["patch"] = new Dictionary<string, object?> { [objectName] = value },
            });
        if (!Ok(reply, out var result)) { ReportFailure(reply, "save"); return; }
        AdoptConfig(result.GetProperty("config"));
    }

    private void TestDetection_Click(object sender, RoutedEventArgs e) => _ = TestDetectionAsync();

    private async Task TestDetectionAsync()
    {
        var reply = await _engine.CallAsync("test_detection");
        if (!Ok(reply, out var result)) { ReportFailure(reply, "detection test"); return; }
        var path = result.GetProperty("path").GetString() ?? "";
        _model.AddLog("info",
            $"detection test: {result.GetProperty("detail").GetString()} - saved {path}");
        try
        {
            System.Diagnostics.Process.Start(
                new System.Diagnostics.ProcessStartInfo(path) { UseShellExecute = true });
        }
        catch (Exception)
        {
            // No image viewer associated; the path in the log is enough.
        }
    }

    private void SampleColour_Click(object sender, RoutedEventArgs e)
    {
        if (sender is Button { Tag: string attr }) _ = SampleColourAsync(attr);
    }

    private async Task SampleColourAsync(string attr)
    {
        var origin = await ClientOriginAsync();
        if (origin is null) return;
        var points = OverlayWindow.PickPoints(this, new[] { "the colour to sample" });
        if (points.Count == 0) return;
        var (x, y) = points.Values.First();
        var (ox, oy) = origin.Value;
        var reply = await _engine.CallAsync("sample_color",
            new Dictionary<string, object?> { ["x"] = x - ox, ["y"] = y - oy, ["attr"] = attr });
        if (!Ok(reply, out var result)) { ReportFailure(reply, "colour sample"); return; }
        var rgb = result.GetProperty("rgb").EnumerateArray().Select(v => v.GetInt32()).ToArray();
        _model.AddLog("info", $"{attr} set to RGB ({rgb[0]}, {rgb[1]}, {rgb[2]})");
        await ReloadConfigAsync();
    }

    private void BaitShop_Click(object sender, RoutedEventArgs e) =>
        _ = PickBaitAsync(BaitShopPoints,
            new[] { "shop_open", "shop_buy_common", "shop_confirm", "shop_close" },
            "bait shop");

    private void BaitCraft_Click(object sender, RoutedEventArgs e) =>
        _ = PickBaitAsync(BaitCraftPoints,
            new[] { "craft_open", "craft_select_recipe", "craft_select_amount",
                    "craft_button", "craft_confirm", "craft_close" },
            "crafting");

    private async Task PickBaitAsync(string[] labels, string[] attrs, string what)
    {
        var origin = await ClientOriginAsync();
        if (origin is null) return;
        var picked = OverlayWindow.PickPoints(this, labels);
        var (ox, oy) = origin.Value;

        // A skipped point is stored as empty, which is how the engine reads
        // "not calibrated" - leaving the old value would be worse than blank.
        var patch = new Dictionary<string, object?>();
        for (var i = 0; i < labels.Length; i++)
            patch[attrs[i]] = picked.TryGetValue(labels[i], out var point)
                ? new[] { point.X - ox, point.Y - oy }
                : Array.Empty<int>();

        await PatchAsync("bait", patch);
        _model.AddLog("info", $"{what} points saved: {picked.Count}/{labels.Length}");
    }

    private void FruitTemplate_Click(object sender, RoutedEventArgs e) => _ = FruitTemplateAsync();

    private async Task FruitTemplateAsync()
    {
        var region = await PickRegionAsync(
            "Drag a box around the devil fruit icon - just the icon, not the whole slot");
        if (region is null)
        {
            _model.AddLog("info", "fruit icon snip cancelled");
            return;
        }
        var (x1, y1, x2, y2) = region.Value;
        var reply = await _engine.CallAsync("save_template",
            new Dictionary<string, object?> { ["x1"] = x1, ["y1"] = y1, ["x2"] = x2, ["y2"] = y2 });
        if (!Ok(reply, out var result)) { ReportFailure(reply, "fruit icon"); return; }
        _model.AddLog("info",
            $"fruit icon saved ({result.GetProperty("width").GetInt32()}x" +
            $"{result.GetProperty("height").GetInt32()})");
        await ReloadConfigAsync();
    }

    private void FruitHotbar_Click(object sender, RoutedEventArgs e) => _ = FruitRegionAsync(
        "hotbar_region",
        "Drag across the whole hotbar row. Fruits are found by position inside this box and " +
        "equipped by clicking, so the box only has to contain every slot - it does not need " +
        "to line up with them.",
        "hotbar row set");

    private void FruitBanner_Click(object sender, RoutedEventArgs e) => _ = FruitRegionAsync(
        "banner_region",
        "Drag a box over the top-of-screen banner strip, where 'New Item' and " +
        "'You can only store one of each fruit!' appear",
        "banner strip set");

    private async Task FruitRegionAsync(string attr, string instructions, string done)
    {
        var region = await PickRegionAsync(instructions);
        if (region is null) return;
        var (x1, y1, x2, y2) = region.Value;
        await PatchAsync("fruit", new Dictionary<string, object?>
        {
            [attr] = new Dictionary<string, object?>
            {
                ["x1"] = x1, ["y1"] = y1, ["x2"] = x2, ["y2"] = y2,
            },
        });
        _model.AddLog("info", $"{done} ({x2 - x1}x{y2 - y1})");
    }

    private void FruitStore_Click(object sender, RoutedEventArgs e) => _ = FruitStoreAsync();

    private async Task FruitStoreAsync()
    {
        var origin = await ClientOriginAsync();
        if (origin is null) return;
        const string label = "anywhere on the green Store Fruit button";
        var picked = OverlayWindow.PickPoints(this, new[] { label });
        if (!picked.TryGetValue(label, out var point)) return;
        var (ox, oy) = origin.Value;
        var x = point.X - ox;
        var y = point.Y - oy;
        await PatchAsync("fruit",
            new Dictionary<string, object?> { ["store_point"] = new[] { x, y } });
        _model.AddLog("info",
            $"store button anchor set at ({x},{y}) - the prompt is searched for around it");
    }

    // ---------------------------------------------------- calibration readouts

    private void RefreshCalibrationReadouts()
    {
        if (!_haveConfig) return;

        var region = _config.GetProperty("scan_region");
        var (rx1, ry1) = (region.GetProperty("x1").GetInt32(), region.GetProperty("y1").GetInt32());
        var (rx2, ry2) = (region.GetProperty("x2").GetInt32(), region.GetProperty("y2").GetInt32());
        var valid = rx2 > rx1 && ry2 > ry1;
        _model.RegionLine = valid
            ? $"({rx1},{ry1}) - ({rx2},{ry2})   {rx2 - rx1} x {ry2 - ry1}"
            : "not set - run auto-detect with the gauge on screen";
        _model.RegionBrush = Palette.Named(valid ? "Text2" : "Amber");

        var detection = _config.GetProperty("detection");
        _model.ColourLines =
            $"gauge blue  RGB {Triple(detection.GetProperty("bar_blue"))}\n" +
            $"bar black   RGB {Triple(detection.GetProperty("track_gray"))}\n" +
            $"tolerance   +/- {detection.GetProperty("color_tolerance").GetInt32()} per channel";

        var fruit = _config.GetProperty("fruit");
        var template = fruit.GetProperty("template_path").GetString() ?? "";
        var templatePath = Path.IsPathRooted(template)
            ? template : Path.Combine(_engine.RepoRoot, template);
        _model.FruitLines = string.Join("\n", new[]
        {
            Row("icon", File.Exists(templatePath) ? template : ""),
            Row("hotbar", RegionSize(fruit.GetProperty("hotbar_region"))),
            Row("banner", RegionSize(fruit.GetProperty("banner_region"))),
            Row("store", Point(fruit.GetProperty("store_point"))),
        });
    }

    private static string Row(string name, string detail) =>
        $"{name,-8} {(string.IsNullOrEmpty(detail) ? "not set" : detail)}";

    private static string Triple(JsonElement array) =>
        "(" + string.Join(", ", array.EnumerateArray().Select(v => v.GetInt32())) + ")";

    private static string Point(JsonElement array) =>
        array.GetArrayLength() >= 2
            ? $"({array[0].GetInt32()},{array[1].GetInt32()})" : "";

    private static string RegionSize(JsonElement region)
    {
        var width = region.GetProperty("x2").GetInt32() - region.GetProperty("x1").GetInt32();
        var height = region.GetProperty("y2").GetInt32() - region.GetProperty("y1").GetInt32();
        return width > 0 && height > 0
            ? width.ToString(CultureInfo.InvariantCulture) + "x" +
              height.ToString(CultureInfo.InvariantCulture)
            : "";
    }
}
