using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Globalization;
using System.Runtime.CompilerServices;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Data;
using System.Windows.Media;

namespace GpoMacro;

public abstract class Observable : INotifyPropertyChanged
{
    public event PropertyChangedEventHandler? PropertyChanged;

    protected void Raise([CallerMemberName] string? name = null) =>
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));

    protected bool Set<T>(ref T field, T value, [CallerMemberName] string? name = null)
    {
        if (EqualityComparer<T>.Default.Equals(field, value)) return false;
        field = value;
        Raise(name);
        return true;
    }
}

/// <summary>Theme lookups. Chrome comes from Theme.xaml; anything whose colour
/// carries meaning (a state, an event kind) comes from the engine, so
/// theme.py stays the one place that decides what green means.</summary>
public static class Palette
{
    private static readonly Dictionary<string, SolidColorBrush> Cache = new();

    public static SolidColorBrush Named(string key) =>
        (SolidColorBrush)Application.Current.Resources[key];

    public static SolidColorBrush FromHex(string? hex, string fallbackKey = "Muted")
    {
        if (string.IsNullOrWhiteSpace(hex)) return Named(fallbackKey);
        if (Cache.TryGetValue(hex, out var cached)) return cached;
        try
        {
            var brush = new SolidColorBrush((Color)ColorConverter.ConvertFromString(hex));
            brush.Freeze();
            Cache[hex] = brush;
            return brush;
        }
        catch (FormatException)
        {
            return Named(fallbackKey);
        }
    }
}

// ------------------------------------------------------------------- log line

/// <summary>`HH:MM:SS` then a one-character mark then the message, coloured by
/// kind. Mono throughout, because most lines are numbers.</summary>
public sealed class LogEntry
{
    public LogEntry(string mark, string message, Brush brush)
    {
        Time = DateTime.Now.ToString("HH:mm:ss", CultureInfo.InvariantCulture);
        Mark = mark;
        Message = message;
        Brush = brush;
    }

    public string Time { get; }
    public string Mark { get; }
    public string Message { get; }
    public Brush Brush { get; }
}

/// <summary>True when the bound value is null; lets a style trigger on an
/// empty ImageSource without a bool shadow property.</summary>
public sealed class IsNullConverter : IValueConverter
{
    public object Convert(object? value, Type targetType, object? parameter, CultureInfo culture) =>
        value is null;

    public object ConvertBack(object? value, Type targetType, object? parameter, CultureInfo culture) =>
        throw new NotSupportedException();
}

// ------------------------------------------------------------------ reading

/// <summary>One line of the sidebar footer: a fixed label over a value the
/// tick or the config keeps current. The brush says whether it is good.</summary>
public sealed class Reading : Observable
{
    private string _value = "-";
    private Brush _brush = Brushes.Gray;

    public Reading(string label) => Label = label;

    public string Label { get; }
    public string Value { get => _value; set => Set(ref _value, value); }
    public Brush Brush { get => _brush; set => Set(ref _brush, value); }

    public void Show(string value, string brushKey)
    {
        Value = value;
        Brush = Palette.Named(brushKey);
    }
}

// -------------------------------------------------------------------- setting

/// <summary>One row of the settings form, described by the engine's schema.</summary>
public sealed class SettingField : Observable
{
    private string _text = "";
    private bool _flag;
    private string _saved = "";

    public SettingField(string obj, string attr, string label, string kind, string effect)
    {
        Object = obj;
        Attr = attr;
        Label = label;
        Kind = kind;
        _effect = effect;
    }

    public string Object { get; }
    public string Attr { get; }
    public string Label { get; }
    public string Kind { get; }

    /// <summary>What the knob does, beside it. Usually static from the schema;
    /// the webhook URL's is live, carrying the last delivery's result.</summary>
    private string _effect;
    public string Effect { get => _effect; set => Set(ref _effect, value); }

    private Brush _effectBrush = Palette.Named("Faint");
    public Brush EffectBrush { get => _effectBrush; set => Set(ref _effectBrush, value); }

    private bool _isVisible = true;
    public bool IsVisible { get => _isVisible; set => Set(ref _isVisible, value); }

    public bool IsBool => Kind == "bool";
    public bool IsSecret => Kind == "secret";

    public string Text
    {
        get => _text;
        set { if (Set(ref _text, value)) Raise(nameof(IsDirty)); }
    }

    public bool Flag
    {
        get => _flag;
        set { if (Set(ref _flag, value)) Raise(nameof(IsDirty)); }
    }

    /// <summary>Nothing here saves as you type, so the form has to say so.</summary>
    public bool IsDirty => Current != _saved;

    private string Current => IsBool
        ? (_flag ? "true" : "false")
        : _text.Trim();

    /// <summary>Adopt a value from the engine as the new saved baseline.</summary>
    public void Load(System.Text.Json.JsonElement value)
    {
        Edit(value);
        _saved = Current;
        Raise(nameof(IsDirty));
    }

    /// <summary>Put a value in the field as if the user had typed it: the
    /// baseline stays, so the row reads dirty until saved.</summary>
    public void Edit(System.Text.Json.JsonElement value)
    {
        if (IsBool)
            Flag = value.ValueKind == System.Text.Json.JsonValueKind.True;
        else
            Text = value.ValueKind switch
            {
                System.Text.Json.JsonValueKind.String => value.GetString() ?? "",
                System.Text.Json.JsonValueKind.Null => "",
                _ => value.ToString(),
            };
    }

    /// <summary>The typed value, ready for set_config. Throws on a value the
    /// field's kind cannot hold, so a bad number is reported rather than
    /// silently written as a string.</summary>
    public object? Value() => Kind switch
    {
        "bool" => Flag,
        "int" => (int)Math.Round(double.Parse(Text.Trim(), CultureInfo.InvariantCulture)),
        "float" => double.Parse(Text.Trim(), CultureInfo.InvariantCulture),
        _ => Text.Trim(),
    };
}

public sealed class SettingsSection : Observable
{
    public SettingsSection(string name, string note, bool isAdvanced)
    {
        Name = name;
        Note = note;
        HasNote = !string.IsNullOrWhiteSpace(note);
        IsAdvanced = isAdvanced;
    }

    public string Name { get; }

    /// <summary>Section headings are small-caps mono, like every other label.</summary>
    public string Title => Name.ToUpperInvariant();

    public string Note { get; }
    public bool HasNote { get; }
    public bool IsWebhook => Name == "Webhook";

    /// <summary>Folded behind "Show advanced": knobs that only matter once
    /// something is already broken.</summary>
    public bool IsAdvanced { get; }

    private bool _isVisible = true;
    public bool IsVisible { get => _isVisible; set => Set(ref _isVisible, value); }

    public ObservableCollection<SettingField> Fields { get; } = new();

    /// <summary>Hide fields whose label misses the filter, and the section
    /// when advanced is folded or no field survived.</summary>
    public void Filter(string text, bool showAdvanced)
    {
        var any = false;
        foreach (var field in Fields)
        {
            field.IsVisible = text.Length == 0
                || field.Label.Contains(text, StringComparison.OrdinalIgnoreCase);
            any |= field.IsVisible;
        }
        IsVisible = any && (showAdvanced || !IsAdvanced);
    }
}

// ---------------------------------------------------------------- calibration

public enum CalibrationState { NotSet, Set, Failed, Unverifiable }

/// <summary>A button on a calibration card. The tag names the handler.</summary>
public sealed record CalibrationAction(string Label, string Tag);

/// <summary>One calibration card. The shell owns the words and the buttons;
/// the state, readout and thumbnail are refreshed from the config, and a
/// failed test marks the card Failed with the reason. The mark word and its
/// brush both derive from the state, so colour is never the only signal.</summary>
public sealed class CalibrationSection : Observable
{
    public CalibrationSection(string key, string title, string note, bool isOptional,
                              CalibrationAction primary, params CalibrationAction[] extras)
    {
        Key = key;
        Title = title;
        Note = note;
        IsOptional = isOptional;
        Primary = primary;
        Extras = extras;
    }

    public string Key { get; }
    public string Title { get; }
    public string Note { get; }
    public bool IsOptional { get; }
    public CalibrationAction Primary { get; }
    public CalibrationAction[] Extras { get; }

    private CalibrationState _state;
    public CalibrationState State => _cannotVerify ? CalibrationState.Unverifiable : _state;

    /// <summary>What the config says, window or no window.</summary>
    public CalibrationState BaseState => _state;

    /// <summary>No Roblox window: whatever is stored, nothing can be checked.</summary>
    private bool _cannotVerify;
    public bool CannotVerify
    {
        get => _cannotVerify;
        set { if (_cannotVerify != value) { _cannotVerify = value; Show(_state, _faultText); } }
    }

    private string _readout = "";
    public string Readout { get => _readout; set => Set(ref _readout, value); }

    private string _faultText = "";
    public string FaultText { get => _faultText; set => Set(ref _faultText, value); }
    public bool HasFault => _faultText.Length > 0;

    /// <summary>Region shows the last test capture; colours show two swatches.
    /// Null means no thumbnail column at all.</summary>
    private Brush? _thumb;
    public Brush? Thumb { get => _thumb; set { if (Set(ref _thumb, value)) Raise(nameof(HasThumb)); } }
    public bool HasThumb => _thumb is not null;

    private string _thumbTag = "";
    public string ThumbTag { get => _thumbTag; set => Set(ref _thumbTag, value); }

    private bool _isOpen;
    public bool IsOpen
    {
        get => _isOpen;
        set { if (Set(ref _isOpen, value)) { Raise(nameof(Chevron)); Raise(nameof(EdgeBrush)); } }
    }

    private bool _isMoreOpen;
    public bool IsMoreOpen
    {
        get => _isMoreOpen;
        set { if (Set(ref _isMoreOpen, value)) Raise(nameof(MoreLabel)); }
    }

    public string Chevron => _isOpen ? "v" : ">";
    public Brush EdgeBrush => Palette.Named(_isOpen ? "LineStrong" : "Line");
    public string MoreLabel => _isMoreOpen ? "fewer actions" : "more actions";

    public void Show(CalibrationState state, string faultText = "")
    {
        _state = state;
        FaultText = faultText;
        foreach (var name in new[] { nameof(State), nameof(Mark), nameof(MarkBrush),
                                     nameof(TitleBrush), nameof(ReadoutBrush), nameof(ThumbEdge),
                                     nameof(HasFault), nameof(FaultWord), nameof(FaultBrush) })
            Raise(name);
    }

    public string Mark => State switch
    {
        CalibrationState.Set => "set",
        CalibrationState.Failed => "set \u00b7 test failed",
        CalibrationState.Unverifiable => "cannot verify",
        _ => IsOptional ? "not set \u00b7 optional" : "not set",
    };

    public Brush MarkBrush => Palette.Named(State switch
    {
        CalibrationState.Set => "Green",
        CalibrationState.Failed => "Red",
        CalibrationState.Unverifiable => "Faint",
        _ => IsOptional ? "Faint" : "Amber",
    });

    /// <summary>Optional and unset is demoted, not hidden.</summary>
    public Brush TitleBrush => Palette.Named(IsOptional && State == CalibrationState.NotSet ? "Muted" : "Text");
    public Brush ReadoutBrush => Palette.Named(State == CalibrationState.NotSet ? "Faint" : "Text2");
    public Brush ThumbEdge => Palette.Named(State == CalibrationState.Set ? "GreenDim" : "Line");
    public string FaultWord => State == CalibrationState.Failed ? "why" : "note";
    public Brush FaultBrush => Palette.Named(State == CalibrationState.Failed ? "Red" : "Amber");
}

/// <summary>A checkbox spans the row; everything else is label plus field.</summary>
public sealed class FieldTemplateSelector : DataTemplateSelector
{
    public DataTemplate? BoolTemplate { get; set; }
    public DataTemplate? TextTemplate { get; set; }
    public DataTemplate? SecretTemplate { get; set; }

    public override DataTemplate? SelectTemplate(object item, DependencyObject container) =>
        item is not SettingField field ? base.SelectTemplate(item, container)
        : field.IsBool ? BoolTemplate
        : field.IsSecret ? SecretTemplate
        : TextTemplate;
}
