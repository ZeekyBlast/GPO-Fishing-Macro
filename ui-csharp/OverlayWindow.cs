using System;
using System.Collections.Generic;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Shapes;

namespace GpoMacro;

/// <summary>A rectangle picked off the screen, in physical pixels.</summary>
public readonly record struct ScreenRect(int X1, int Y1, int X2, int Y2)
{
    public int Width => Math.Max(0, X2 - X1);
    public int Height => Math.Max(0, Y2 - Y1);
    public bool IsValid => Width > 0 && Height > 0;
}

/// <summary>
/// The calibration overlay: one dimmed sheet across every monitor, on which
/// the user drags a box or clicks a series of points.
///
/// It reports physical screen pixels and knows nothing about the Roblox
/// window - the caller subtracts the client origin the engine reports, because
/// the engine is the one that decides which window "Roblox" means.
///
/// Coordinates come from the mouse event itself via PointToScreen, never from
/// GetCursorPos: by the time a handler runs the pointer has already moved on,
/// so a quick press-and-drag would anchor several pixels from where the user
/// pressed. PointToScreen also does the DIP-to-device conversion, so a scaled
/// display lands on the same pixel the user clicked.
/// </summary>
public sealed class OverlayWindow : Window
{
    private readonly Canvas _canvas = new();
    private readonly Rectangle _marquee;
    private readonly Border _caption;
    private readonly TextBlock _captionText;

    private readonly List<string> _remaining = new();
    private readonly Dictionary<string, (int X, int Y)> _points = new();
    private readonly bool _pickMode;

    private (int X, int Y) _anchor;
    private Point _anchorInCanvas;
    private bool _dragging;

    public ScreenRect? Region { get; private set; }
    public IReadOnlyDictionary<string, (int X, int Y)> Points => _points;

    private OverlayWindow(string instructions, IEnumerable<string>? labels)
    {
        _pickMode = labels is not null;
        if (labels is not null) _remaining.AddRange(labels);

        WindowStyle = WindowStyle.None;
        ResizeMode = ResizeMode.NoResize;
        AllowsTransparency = true;
        ShowInTaskbar = false;
        Topmost = true;
        Background = new SolidColorBrush(Color.FromArgb(_pickMode ? (byte)0x73 : (byte)0x4D,
                                                        0x0C, 0x0E, 0x10));
        Cursor = Cursors.Cross;

        _marquee = new Rectangle
        {
            Stroke = Brush("Green"),
            StrokeThickness = 2,
            Fill = new SolidColorBrush(Color.FromArgb(0x33, 0x4E, 0xC9, 0x8A)),
            Visibility = Visibility.Collapsed,
        };
        _canvas.Children.Add(_marquee);

        _captionText = new TextBlock
        {
            Text = instructions,
            FontFamily = (FontFamily)Application.Current.Resources["Sans"],
            FontSize = 13,
            Foreground = Brush("Text"),
            TextWrapping = TextWrapping.Wrap,
            MaxWidth = 420,
        };
        // The caption follows the cursor: on a multi-monitor desktop the
        // top-left corner of the virtual screen is often on another display.
        _caption = new Border
        {
            Background = Brush("Surface"),
            BorderBrush = Brush("Line"),
            BorderThickness = new Thickness(1),
            CornerRadius = new CornerRadius(4),
            Padding = new Thickness(10, 7, 10, 7),
            Child = _captionText,
            IsHitTestVisible = false,
        };
        _canvas.Children.Add(_caption);
        Content = _canvas;

        MouseMove += OnMouseMove;
        MouseLeftButtonDown += OnLeftDown;
        MouseLeftButtonUp += OnLeftUp;
        MouseRightButtonDown += OnRightDown;
        KeyDown += OnKeyDown;
        SourceInitialized += OnSourceInitialized;
        Loaded += (_, _) => { Activate(); Focus(); RefreshCaption(); };
    }

    private static SolidColorBrush Brush(string key) =>
        (SolidColorBrush)Application.Current.Resources[key];

    private void OnSourceInitialized(object? sender, EventArgs e)
    {
        var (x, y, width, height) = Native.VirtualScreen();
        Native.PlacePhysical(new WindowInteropHelper(this).Handle, x, y, width, height);
    }

    // ------------------------------------------------------------------ input

    /// <summary>The event's own position in physical screen pixels.</summary>
    private (int X, int Y) Physical(MouseEventArgs e)
    {
        var screen = PointToScreen(e.GetPosition(this));
        return ((int)Math.Round(screen.X), (int)Math.Round(screen.Y));
    }

    private void OnMouseMove(object sender, MouseEventArgs e)
    {
        var position = e.GetPosition(_canvas);
        Canvas.SetLeft(_caption, position.X + 18);
        Canvas.SetTop(_caption, position.Y + 20);

        if (!_dragging) return;
        var left = Math.Min(_anchorInCanvas.X, position.X);
        var top = Math.Min(_anchorInCanvas.Y, position.Y);
        Canvas.SetLeft(_marquee, left);
        Canvas.SetTop(_marquee, top);
        _marquee.Width = Math.Abs(position.X - _anchorInCanvas.X);
        _marquee.Height = Math.Abs(position.Y - _anchorInCanvas.Y);
    }

    private void OnLeftDown(object sender, MouseButtonEventArgs e)
    {
        if (_pickMode)
        {
            RecordPoint(Physical(e));
            return;
        }
        _dragging = true;
        _anchor = Physical(e);
        _anchorInCanvas = e.GetPosition(_canvas);
        _marquee.Width = _marquee.Height = 0;
        _marquee.Visibility = Visibility.Visible;
        CaptureMouse();
    }

    private void OnLeftUp(object sender, MouseButtonEventArgs e)
    {
        if (_pickMode || !_dragging) return;
        _dragging = false;
        ReleaseMouseCapture();
        var (x, y) = Physical(e);
        var region = new ScreenRect(Math.Min(_anchor.X, x), Math.Min(_anchor.Y, y),
                                    Math.Max(_anchor.X, x), Math.Max(_anchor.Y, y));
        Region = region.IsValid ? region : null;
        DialogResult = Region is not null;
        Close();
    }

    private void RecordPoint((int X, int Y) at)
    {
        if (_remaining.Count == 0) return;
        _points[_remaining[0]] = at;
        _remaining.RemoveAt(0);
        if (_remaining.Count == 0)
        {
            DialogResult = _points.Count > 0;
            Close();
            return;
        }
        RefreshCaption();
    }

    private void OnRightDown(object sender, MouseButtonEventArgs e)
    {
        if (!_pickMode || _remaining.Count == 0) return;
        _remaining.RemoveAt(0);          // skip this one
        if (_remaining.Count == 0)
        {
            DialogResult = _points.Count > 0;
            Close();
            return;
        }
        RefreshCaption();
    }

    private void OnKeyDown(object sender, KeyEventArgs e)
    {
        if (e.Key != Key.Escape) return;
        DialogResult = _pickMode && _points.Count > 0;
        Close();
    }

    private void RefreshCaption()
    {
        if (!_pickMode) return;
        _captionText.Text = _remaining.Count > 0
            ? $"Click: {_remaining[0]}\nright-click skips · Esc stops"
            : "done";
    }

    // ----------------------------------------------------------------- callers

    /// <summary>Drag a box. Returns null if cancelled or empty.</summary>
    public static ScreenRect? DragRegion(Window owner, string instructions)
    {
        var overlay = new OverlayWindow(instructions + "\nDrag a box · Esc cancels", null)
        {
            Owner = owner,
        };
        overlay.ShowDialog();
        return overlay.Region;
    }

    /// <summary>Click one point per label, in order. Skipped labels are absent
    /// from the result.</summary>
    public static IReadOnlyDictionary<string, (int X, int Y)> PickPoints(
        Window owner, IEnumerable<string> labels)
    {
        var overlay = new OverlayWindow("", labels) { Owner = owner };
        overlay.ShowDialog();
        return overlay.Points;
    }
}
