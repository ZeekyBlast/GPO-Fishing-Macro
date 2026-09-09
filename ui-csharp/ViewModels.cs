using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Globalization;
using System.Runtime.CompilerServices;
using System.Windows;
using System.Windows.Controls;
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

// -------------------------------------------------------------------- setting

/// <summary>One row of the settings form, described by the engine's schema.</summary>
public sealed class SettingField : Observable
{
    private string _text = "";
    private bool _flag;
    private string _saved = "";

    public SettingField(string obj, string attr, string label, string kind)
    {
        Object = obj;
        Attr = attr;
        Label = label;
        Kind = kind;
    }

    public string Object { get; }
    public string Attr { get; }
    public string Label { get; }
    public string Kind { get; }

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
        if (IsBool)
        {
            _flag = value.ValueKind == System.Text.Json.JsonValueKind.True;
            Raise(nameof(Flag));
        }
        else
        {
            _text = value.ValueKind switch
            {
                System.Text.Json.JsonValueKind.String => value.GetString() ?? "",
                System.Text.Json.JsonValueKind.Null => "",
                _ => value.ToString(),
            };
            Raise(nameof(Text));
        }
        _saved = Current;
        Raise(nameof(IsDirty));
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

public sealed class SettingsSection
{
    public SettingsSection(string name, string note)
    {
        Name = name;
        Note = note;
        HasNote = !string.IsNullOrWhiteSpace(note);
    }

    public string Name { get; }

    /// <summary>Section headings are small-caps mono, like every other label.</summary>
    public string Title => Name.ToUpperInvariant();

    public string Note { get; }
    public bool HasNote { get; }
    public bool IsWebhook => Name == "Webhook";
    public ObservableCollection<SettingField> Fields { get; } = new();
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
