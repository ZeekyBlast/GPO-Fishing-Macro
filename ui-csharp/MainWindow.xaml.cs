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
using System.Windows.Media;
using System.Windows.Media.Imaging;

namespace GpoMacro;

public partial class MainWindow : Window
{
    private static readonly string[] BaitShopPoints =
        { "the quantity box in the barrel's dialog", "Confirm", "Cancel (optional)" };

    private readonly MainViewModel _model = new();
    private readonly Engine _engine = new();
    private readonly Updater _updater = new();
    private UpdateInfo? _pendingUpdate;

    private JsonElement _config;
    private JsonElement _defaults;
    private bool _haveConfig;
    private string _toggleKey = "f6";
    private bool _livePreview;
    private bool _closing;
    private bool _scrollQueued;
    private bool _checkedForUpdates;
    private bool _setupDecided;
    private readonly List<ListBox> _railLogs = new();

    public MainWindow()
    {
        InitializeComponent();
        DataContext = _model;

        _model.Log.CollectionChanged += ScrollLogToEnd;
        _model.PropertyChanged += (_, args) =>
        {
            if (args.PropertyName == nameof(MainViewModel.NoGauge)) RefreshCalibrationReadouts();
        };
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
            if (_model.Log.Count == 0) return;
            LogList.ScrollIntoView(_model.Log[^1]);
            foreach (var rail in _railLogs) rail.ScrollIntoView(_model.Log[^1]);
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
                    $"Welcome - calibrate once (Calibration, on the left), then Start. " +
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
                section = new SettingsSection(name, note,
                    entry.TryGetProperty("advanced", out var adv) && adv.GetBoolean());
                byName[name] = section;
                _model.Sections.Add(section);
            }
            var field = new SettingField(
                entry.GetProperty("obj").GetString() ?? "",
                entry.GetProperty("attr").GetString() ?? "",
                entry.GetProperty("label").GetString() ?? "",
                entry.GetProperty("kind").GetString() ?? "str",
                entry.TryGetProperty("effect", out var effect) ? effect.GetString() ?? "" : "");
            field.PropertyChanged += (_, args) =>
            {
                if (args.PropertyName == nameof(SettingField.IsDirty)) _model.RefreshDirty();
            };
            section.Fields.Add(field);
            if (field.Object == "webhook" && field.Attr == "url") _model.WebhookField = field;
        }
        _defaults = hello.TryGetProperty("defaults", out var defaults) ? defaults.Clone() : default;
        _model.RefreshFilter();
    }

    /// <summary>Put one section back to the engine's defaults. Nothing is
    /// written: the rows go dirty and Save is still the only way out.</summary>
    private void ResetSection_Click(object sender, RoutedEventArgs e)
    {
        if (_defaults.ValueKind != JsonValueKind.Object) return;
        if ((sender as FrameworkElement)?.DataContext is not SettingsSection section) return;
        foreach (var field in section.Fields)
            if (_defaults.TryGetProperty(field.Object, out var group)
                && group.TryGetProperty(field.Attr, out var value))
                field.Edit(value);
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
        var controller = _config.GetProperty("controller");
        _model.LeadLine = string.Format(CultureInfo.InvariantCulture,
            "Bar lead {0:0.00}s · fish lead {1:0.00}s · tune Bar lead in Settings",
            controller.GetProperty("bar_lead").GetDouble(),
            controller.GetProperty("fish_lead").GetDouble());
        _model.RefreshDirty();
        RefreshCalibrationReadouts();

        // The setup runs once: on the first hello without the flag, and never
        // again on a later config reload.
        if (!_setupDecided)
        {
            _setupDecided = true;
            var done = ui.TryGetProperty("first_run_complete", out var flag) && flag.GetBoolean();
            if (!done)
            {
                _model.SetupStep = 1;
                _model.SetupActive = true;
                _model.AddLog("info", "setup opened - six steps, about two minutes");
            }
        }

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

    private async Task TestDetectionAsync()
    {
        var reply = await _engine.CallAsync("test_detection");
        if (!Ok(reply, out var result)) { ReportFailure(reply, "detection test"); return; }
        var found = result.GetProperty("found").GetBoolean();
        var detail = result.GetProperty("detail").GetString() ?? "";
        _lastTest = (DateTime.Now, found, detail);
        _model.AddLog(found ? "info" : "warn",
            $"detection test: {detail} - saved {result.GetProperty("path").GetString()}");
        RefreshCalibrationReadouts();
    }

    /// <summary>Every calibration button lands here; the tag says which.</summary>
    private void CalAction_Click(object sender, RoutedEventArgs e)
    {
        var tag = (sender as FrameworkElement)?.Tag as string;
        _ = tag switch
        {
            "test" => TestDetectionAsync(),
            "auto" => AutoDetectAsync(),
            "drag" => DragRegionAsync(),
            "resample" => ResampleBothAsync(),
            "blue" => SampleColourAsync("bar_blue"),
            "black" => SampleColourAsync("track_gray"),
            "craft" => CraftWizardAsync(),
            "menu" => BaitMenuAsync(),
            "counter" => BaitCounterAsync(),
            "row" => BaitRowAsync(),
            "barrel" => PickBaitAsync(BaitShopPoints,
                new[] { "shop_quantity", "shop_confirm", "shop_cancel" }, "barrel"),
            "badge" => BaitPromptAsync(),
            "storage" => StorageWizardAsync(),
            "icon" => FruitTemplateAsync(),
            "hotbar" => FruitHotbarAsync(),
            "banner" => FruitBannerAsync(),
            "store" => FruitStoreAsync(),
            _ => Task.CompletedTask,
        };
    }

    /// <summary>The fault's own fix, from where the fault appears.</summary>
    private async void Recovery_Click(object sender, RoutedEventArgs e)
    {
        switch (_model.RecoveryTag)
        {
            case "recheck":
                var reply = await _engine.CallAsync("recheck_window");
                if (!Ok(reply, out var result)) { ReportFailure(reply, "recheck"); return; }
                _model.AddLog(result.GetProperty("found").GetBoolean() ? "info" : "warn",
                    result.GetProperty("found").GetBoolean()
                        ? "Roblox window found again - ready to start"
                        : "still no Roblox window - launch GPO, then recheck");
                break;
            case "calibration":
                _model.SelectedScreen = MainViewModel.CalibrationScreen;
                _model.Region.IsOpen = true;
                break;
        }
    }

    // ------------------------------------------------------------------ setup

    /// <summary>Each proof rail's log follows the tail like the dashboard's.</summary>
    private void RailLog_Loaded(object sender, RoutedEventArgs e)
    {
        if (sender is ListBox list && !_railLogs.Contains(list)) _railLogs.Add(list);
    }

    private TaskCompletionSource<bool>? _prompt;

    private void SetupNext_Click(object sender, RoutedEventArgs e)
    {
        if (_prompt is not null) { _prompt.TrySetResult(true); return; }
        if (_model.SetupStep == 6) _ = FinishSetupAsync(skipped: false);
        else
        {
            if (_model.SetupStep == 1) _model.AddLog("info", "risk acknowledged");
            _model.SetupStep++;
        }
    }

    private void SetupBack_Click(object sender, RoutedEventArgs e)
    {
        if (_prompt is not null) _prompt.TrySetResult(false);
        else _model.SetupStep--;
    }

    private void SetupSkip_Click(object sender, RoutedEventArgs e)
    {
        if (_prompt is not null) _prompt.TrySetResult(false);
        else _ = FinishSetupAsync(skipped: true);
    }

    private void SetupPip_Click(object sender, RoutedEventArgs e)
    {
        if (_prompt is null && (sender as FrameworkElement)?.DataContext is Reading pip)
            _model.SetupStep = _model.SetupPips.IndexOf(pip) + 1;
    }

    private void RerunSetup_Click(object sender, RoutedEventArgs e)
    {
        _model.SetupStep = 1;
        _model.SetupActive = true;
    }

    /// <summary>Skipped or finished, the flag is written either way: the
    /// dashboard's amber hint carries whatever is still undone.</summary>
    private async Task FinishSetupAsync(bool skipped)
    {
        await PatchAsync("ui", new Dictionary<string, object?> { ["first_run_complete"] = true });
        _model.SetupActive = false;
        _model.SelectedScreen = MainViewModel.DashboardScreen;
        _model.AddLog("info", skipped
            ? "setup skipped - the Calibration screen has every step"
            : "setup complete - settings.json written");
    }

    private void CalToggle_Click(object sender, RoutedEventArgs e)
    {
        if ((sender as FrameworkElement)?.DataContext is CalibrationSection card)
            card.IsOpen = !card.IsOpen;
    }

    private void CalMore_Click(object sender, RoutedEventArgs e)
    {
        if ((sender as FrameworkElement)?.DataContext is CalibrationSection card)
            card.IsMoreOpen = !card.IsMoreOpen;
    }

    private async Task<bool> SampleColourAsync(string attr)
    {
        var origin = await ClientOriginAsync();
        if (origin is null) return false;
        var what = attr == "bar_blue" ? "the gauge's blue, inside the gauge"
                                      : "the bar's black, on the bar itself";
        var points = OverlayWindow.PickPoints(this, new[] { what });
        if (points.Count == 0) return false;
        var (x, y) = points.Values.First();
        var (ox, oy) = origin.Value;
        var reply = await _engine.CallAsync("sample_color",
            new Dictionary<string, object?> { ["x"] = x - ox, ["y"] = y - oy, ["attr"] = attr });
        if (!Ok(reply, out var result)) { ReportFailure(reply, "colour sample"); return false; }
        var rgb = result.GetProperty("rgb").EnumerateArray().Select(v => v.GetInt32()).ToArray();
        _model.AddLog("info", $"{attr} set to RGB ({rgb[0]}, {rgb[1]}, {rgb[2]})");
        await ReloadConfigAsync();
        return true;
    }

    /// <summary>Sen's menu, one game state at a time.
    ///
    /// The craft points are never all on screen together: Yes lives in the
    /// dialogue, the fish list only exists after + is clicked, the ... bubble
    /// only after the X. The overlay covers the game while it is up, so each
    /// step first tells the user how to get the game into the right state,
    /// waits for OK, then picks only what that state shows. Every point is
    /// verified on screen before the engine clicks it, so one a little off
    /// reports "did not appear" rather than clicking into the dock.</summary>
    private async Task CraftWizardAsync()
    {
        const string bait = "bait";
        try
        {
            if (!await StepAsync(1, "the dialogue",
                    "Stand at Blacksmith Sen and press T so his \"are you interested?\" " +
                    "dialogue is up. Leave it open, then pick the Yes button.")) return;
            if (!await PickBaitAsync(new[] { "the Yes button" }, new[] { "dialog_yes" },
                    "dialogue")) return;

            if (!await StepAsync(2, "the menu",
                    "Click Yes, then click your bait's row (Rare or Legendary Fish Bait) so a " +
                    "red 0/N counter shows under the + slot. Leave it like that.\n\n" +
                    "You will pick four points, then drag two boxes.")) return;
            if (!await PickBaitAsync(
                    new[] { "your bait's row", "the + slot", "anywhere on the green CRAFT button",
                            "the red X" },
                    new[] { "craft_recipe", "craft_add", "craft_button", "craft_close" },
                    "menu")) return;
            if (!await RegionAsync(bait, "menu_region",
                    "Drag a box over the whole of Sen's craft menu. It only has to cover most of it.",
                    "craft menu region set")) return;
            if (!await RegionAsync(bait, "craft_counter_region",
                    "Drag a tight box around the red 0/N counter under the + slot - just the " +
                    "number, not the + itself.", "material counter set")) return;

            if (!await StepAsync(3, "the fish list",
                    "Click the + slot once so the list of your eligible fish opens beside the " +
                    "menu (you need at least one fish of that tier). Leave it open."))
                return;
            if (!await PickBaitAsync(new[] { "the first fish in the list" }, new[] { "craft_pick" },
                    "fish list")) return;

            if (!await StepAsync(4, "closing",
                    "Click + again to close the list, then click the red X. Sen's \"...\" bubble " +
                    "stays at the bottom of the screen - leave it there.")) return;
            if (!await PickBaitAsync(new[] { "the ... bubble" }, new[] { "dialog_end" },
                    "bubble")) return;

            if (!await StepAsync(5, "the bait row",
                    "Click the ... bubble to end the conversation, then hold your fishing rod " +
                    "so the Fishing Baits panel shows.")) return;
            if (!await PickBaitAsync(new[] { "your bait's row in the Fishing Baits panel" },
                    new[] { "bait_select" }, "bait row")) return;

            _model.AddLog("info", "crafting calibrated - turn on Auto-craft in Settings");
        }
        finally
        {
            _prompt = null;
            _model.EndPrompt();
        }
    }

    /// <summary>Tell the user how to set the game up for the next pick, in the
    /// window rather than a MessageBox: the setup panel, one instruction at a
    /// time, with the rail's log showing what each pick saved. Nothing blocks,
    /// so they can go and do it, then come back and press Pick.</summary>
    private Task<bool> StepAsync(int index, string title, string instructions)
    {
        _prompt = new TaskCompletionSource<bool>();
        _model.ShowPrompt("crafting", index, 5, title, instructions);
        return _prompt.Task;
    }

    private Task<bool> BaitRowAsync() =>
        PickBaitAsync(new[] { "your bait's row in the Fishing Baits panel (rod held)" },
            new[] { "bait_select" }, "bait row");

    private Task<bool> BaitMenuAsync() => RegionAsync(
        "bait", "menu_region",
        "Drag a box over the whole of Sen's craft menu. It only has to cover most of it.",
        "craft menu region set");

    private Task<bool> BaitCounterAsync() => RegionAsync(
        "bait", "craft_counter_region",
        "Drag a tight box around the N/M counter under the + slot - the red 0/1 that " +
        "turns green when it is filled. Just the number, not the + itself.",
        "material counter set");

    private async Task BaitPromptAsync()
    {
        var region = await PickRegionAsync(
            "Drag a box around the white T key badge on Sen's prompt - just the badge");
        if (region is null) return;
        var (x1, y1, x2, y2) = region.Value;
        var path = _config.GetProperty("bait").GetProperty("prompt_template").GetString();
        var reply = await _engine.CallAsync("save_template",
            new Dictionary<string, object?>
                { ["x1"] = x1, ["y1"] = y1, ["x2"] = x2, ["y2"] = y2, ["path"] = path });
        if (!Ok(reply, out var result)) { ReportFailure(reply, "prompt badge"); return; }
        _model.AddLog("info",
            $"prompt badge saved ({result.GetProperty("width").GetInt32()}x" +
            $"{result.GetProperty("height").GetInt32()})");
        await ReloadConfigAsync();
    }

    /// <summary>Pick the labelled points and store them. True only if every
    /// one was picked, so a wizard stops where the user stopped.</summary>
    private async Task<bool> PickBaitAsync(string[] labels, string[] attrs, string what)
    {
        var origin = await ClientOriginAsync();
        if (origin is null) return false;
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
        return picked.Count == labels.Length;
    }

    private async Task<bool> FruitTemplateAsync()
    {
        var region = await PickRegionAsync(
            "Drag a box around the devil fruit icon - just the icon, not the whole slot");
        if (region is null)
        {
            _model.AddLog("info", "fruit icon snip cancelled");
            return false;
        }
        var (x1, y1, x2, y2) = region.Value;
        var reply = await _engine.CallAsync("save_template",
            new Dictionary<string, object?> { ["x1"] = x1, ["y1"] = y1, ["x2"] = x2, ["y2"] = y2 });
        if (!Ok(reply, out var result)) { ReportFailure(reply, "fruit icon"); return false; }
        _model.AddLog("info",
            $"fruit icon saved ({result.GetProperty("width").GetInt32()}x" +
            $"{result.GetProperty("height").GetInt32()})");
        await ReloadConfigAsync();
        return true;
    }

    /// <summary>The four fruit picks in the order the macro uses them. Stops
    /// where the user stops.</summary>
    private async Task StorageWizardAsync()
    {
        if (!await FruitTemplateAsync()) return;
        if (!await FruitHotbarAsync()) return;
        if (!await FruitBannerAsync()) return;
        await FruitStoreAsync();
    }

    private async Task ResampleBothAsync()
    {
        if (!await SampleColourAsync("bar_blue")) return;
        await SampleColourAsync("track_gray");
    }

    private Task<bool> FruitHotbarAsync() => RegionAsync(
        "fruit", "hotbar_region",
        "Drag across the whole hotbar row. Fruits are found by position inside this box and " +
        "equipped by clicking, so the box only has to contain every slot - it does not need " +
        "to line up with them.",
        "hotbar row set");

    private Task<bool> FruitBannerAsync() => RegionAsync(
        "fruit", "banner_region",
        "Drag a box over the top-of-screen banner strip, where 'New Item', " +
        "'You can only store one of each fruit!' and Sen's 'no eligible materials' appear",
        "banner strip set");

    private async Task<bool> RegionAsync(string objectName, string attr, string instructions,
                                         string done)
    {
        var region = await PickRegionAsync(instructions);
        if (region is null) return false;
        var (x1, y1, x2, y2) = region.Value;
        await PatchAsync(objectName, new Dictionary<string, object?>
        {
            [attr] = new Dictionary<string, object?>
            {
                ["x1"] = x1, ["y1"] = y1, ["x2"] = x2, ["y2"] = y2,
            },
        });
        _model.AddLog("info", $"{done} ({x2 - x1}x{y2 - y1})");
        return true;
    }

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

    private (DateTime At, bool Found, string Detail)? _lastTest;

    private void RefreshCalibrationReadouts()
    {
        if (!_haveConfig) return;

        var region = _config.GetProperty("scan_region");
        var (rx1, ry1) = (region.GetProperty("x1").GetInt32(), region.GetProperty("y1").GetInt32());
        var (rx2, ry2) = (region.GetProperty("x2").GetInt32(), region.GetProperty("y2").GetInt32());
        var valid = rx2 > rx1 && ry2 > ry1;
        var size = $"{rx2 - rx1} x {ry2 - ry1}";
        _model.RegionReading.Show(valid ? $"{size} \u00b7 set" : "not set", valid ? "Green" : "Amber");
        _model.RailRegion.Show(valid ? $"region ({rx1},{ry1}) - ({rx2},{ry2})" : "region not set",
                               valid ? "Text2" : "Amber");

        // The last test capture is the region's thumbnail and the rail's
        // proof. The engine writes it beside main.py; a stale one still shows
        // what the region looked like last time.
        var capturePath = Path.Combine(_engine.RepoRoot, "test_detection.png");
        var capture = LoadImage(capturePath);
        _model.TestCapture = capture;
        _model.TestCaptureTag = valid ? size : "";
        _model.Region.Thumb = capture is null ? null : new ImageBrush(capture) { Stretch = Stretch.Uniform };
        _model.Region.ThumbTag = capture is null ? "" : "last test";

        var testLine = _lastTest is { } t
            ? (t.Found ? $"tested {Ago(t.At)}, bar and fish read" : $"tested {Ago(t.At)}, {t.Detail}")
            : capture is not null ? $"last test {Ago(File.GetLastWriteTime(capturePath))}"
            : "never tested";
        _model.RailTest.Show(testLine,
            _lastTest is { Found: false } ? "Red" : _lastTest is { Found: true } ? "Green" : "Faint");
        _model.Region.Readout = valid
            ? $"region  ({rx1},{ry1}) - ({rx2},{ry2})\nsize    {size}\n{testLine}"
            : "not set - run auto-detect with the gauge on screen";
        // The engine remembers a failed test or auto-detect across the shell's
        // own record, so either says Failed.
        var noGauge = _model.NoGauge || _lastTest is { Found: false };
        _model.Region.Show(
            !valid ? CalibrationState.NotSet
            : noGauge ? CalibrationState.Failed
            : CalibrationState.Set,
            noGauge
                ? "The test found no run of gauge blue tall enough to be the bar. Start a " +
                  "minigame so the gauge is up and test again. If the gauge was up, the blue " +
                  "was sampled off the sea - re-sample it from inside the gauge."
                : "");
        if (noGauge && valid) _model.Colours.FaultText =
            "If the region is right and the test still fails, this blue is the reason: " +
            "re-sample it from inside the gauge, not the sea. Widening the tolerance " +
            "cannot fix a wrong colour.";
        else _model.Colours.FaultText = "";

        var detection = _config.GetProperty("detection");
        var blue = detection.GetProperty("bar_blue");
        var black = detection.GetProperty("track_gray");
        var tolerance = detection.GetProperty("color_tolerance").GetInt32();
        _model.Colours.Readout =
            $"gauge blue  RGB {Triple(blue)}\n" +
            $"bar black   RGB {Triple(black)}\n" +
            $"tolerance   +/- {tolerance} per channel";
        _model.Colours.Thumb = new LinearGradientBrush(new GradientStopCollection
        {
            new(Rgb(blue), 0), new(Rgb(blue), 0.5), new(Rgb(black), 0.5), new(Rgb(black), 1),
        }, 0);
        _model.Colours.ThumbTag = "sampled";
        _model.Colours.Show(CalibrationState.Set);
        _model.RailColours.Show(
            $"blue {Triple(blue).Replace(" ", "")} \u00b7 bar {Triple(black).Replace(" ", "")}", "Text2");

        var fruit = _config.GetProperty("fruit");
        var template = fruit.GetProperty("template_path").GetString() ?? "";
        var templatePath = Path.IsPathRooted(template)
            ? template : Path.Combine(_engine.RepoRoot, template);
        var fruitParts = new[]
        {
            File.Exists(templatePath) ? template : "",
            RegionSize(fruit.GetProperty("hotbar_region")),
            RegionSize(fruit.GetProperty("banner_region")),
            Point(fruit.GetProperty("store_point")),
        };
        _model.Fruit.Readout = string.Join("\n", new[]
        {
            Row("icon", fruitParts[0]), Row("hotbar", fruitParts[1]),
            Row("banner", fruitParts[2]), Row("store", fruitParts[3]),
        });
        _model.Fruit.Show(fruitParts.All(p => p.Length > 0) ? CalibrationState.Set : CalibrationState.NotSet);

        var bait = _config.GetProperty("bait");
        var badge = bait.GetProperty("prompt_template").GetString() ?? "";
        var badgePath = Path.IsPathRooted(badge) ? badge : Path.Combine(_engine.RepoRoot, badge);
        var craft = PointsSet(bait, "dialog_yes", "craft_recipe", "craft_add", "craft_pick",
                              "craft_button", "craft_close", "dialog_end");
        var barrel = PointsSet(bait, "shop_quantity", "shop_confirm");
        _model.Bait.Readout = string.Join("\n", new[]
        {
            Row("craft", craft),
            Row("menu", RegionSize(bait.GetProperty("menu_region"))),
            Row("counter", RegionSize(bait.GetProperty("craft_counter_region"))),
            Row("bait row", Point(bait.GetProperty("bait_select"))),
            Row("barrel", barrel),
            Row("T badge", File.Exists(badgePath) ? badge : ""),
        });
        _model.Bait.Show(craft.StartsWith("7/") || barrel.StartsWith("2/")
            ? CalibrationState.Set : CalibrationState.NotSet);

        var baitOn = bait.GetProperty("auto_craft").GetBoolean() || bait.GetProperty("auto_buy").GetBoolean();
        var fruitOn = fruit.GetProperty("auto_store").GetBoolean();
        _model.OptionalReading.Show(
            (baitOn, fruitOn) switch
            {
                (true, true) => "bait, fruit on",
                (true, false) => "bait on \u00b7 fruit off",
                (false, true) => "bait off \u00b7 fruit on",
                _ => "bait, fruit off",
            },
            baitOn || fruitOn ? "Text2" : "Faint");

        var set = _model.Calibration.Count(c => c.BaseState == CalibrationState.Set);
        _model.CalibrationSummary =
            !valid ? "The scan region is not set, so nothing can fish yet."
            : _model.Region.BaseState == CalibrationState.Failed
                ? "Set, but the last test could not read the gauge."
            : $"{Words[set]} of four set. Bait upkeep and fruit storage are optional and " +
              $"{(baitOn, fruitOn) switch { (true, true) => "on", (false, false) => "off", _ => "one is on" }}.";
    }

    private static readonly string[] Words = { "None", "One", "Two", "Three", "Four" };

    private static string Ago(DateTime at)
    {
        var span = DateTime.Now - at;
        return span.TotalSeconds < 90 ? "just now"
             : span.TotalMinutes < 90 ? $"{span.TotalMinutes:F0} min ago"
             : span.TotalHours < 36 ? $"{span.TotalHours:F0} h ago"
             : $"{span.TotalDays:F0} days ago";
    }

    private static Color Rgb(JsonElement array)
    {
        var v = array.EnumerateArray().Select(x => (byte)Math.Clamp(x.GetInt32(), 0, 255)).ToArray();
        return v.Length >= 3 ? Color.FromRgb(v[0], v[1], v[2]) : Colors.Black;
    }

    /// <summary>Read a PNG without holding the file, so the engine can
    /// overwrite it on the next test.</summary>
    private static BitmapImage? LoadImage(string path)
    {
        if (!File.Exists(path)) return null;
        try
        {
            var image = new BitmapImage();
            image.BeginInit();
            image.CacheOption = BitmapCacheOption.OnLoad;
            image.CreateOptions = BitmapCreateOptions.IgnoreImageCache;
            image.UriSource = new Uri(path);
            image.EndInit();
            image.Freeze();
            return image;
        }
        catch (Exception)
        {
            return null;
        }
    }

    /// <summary>"3/5 points" for a click sequence, or "" if none are set.</summary>
    private static string PointsSet(JsonElement group, params string[] attrs)
    {
        var set = attrs.Count(a => group.GetProperty(a).GetArrayLength() >= 2);
        return set == 0 ? "" : $"{set}/{attrs.Length} points";
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
