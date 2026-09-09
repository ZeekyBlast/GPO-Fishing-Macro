using System;
using System.Runtime.InteropServices;

namespace GpoMacro;

/// <summary>
/// The few Win32 calls the shell needs.
///
/// Calibration coordinates cross into Python as physical screen pixels, and
/// the manifest pins the process to PerMonitorV2 to match main.py, so
/// "physical pixel" means the same thing on both sides. The overlay converts
/// its own mouse events with PointToScreen; nothing here reads the cursor.
/// </summary>
internal static class Native
{
    [DllImport("user32.dll")]
    public static extern int GetSystemMetrics(int index);

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool SetWindowPos(IntPtr hWnd, IntPtr hWndInsertAfter,
                                           int x, int y, int cx, int cy, uint flags);

    [DllImport("dwmapi.dll")]
    private static extern int DwmSetWindowAttribute(IntPtr hwnd, int attribute,
                                                    ref int value, int size);

    private const int SM_XVIRTUALSCREEN = 76;
    private const int SM_YVIRTUALSCREEN = 77;
    private const int SM_CXVIRTUALSCREEN = 78;
    private const int SM_CYVIRTUALSCREEN = 79;

    private static readonly IntPtr HWND_TOPMOST = new(-1);
    private const uint SWP_SHOWWINDOW = 0x0040;
    private const uint SWP_NOACTIVATE = 0x0010;

    private const int DWMWA_USE_IMMERSIVE_DARK_MODE = 20;

    /// <summary>The whole desktop across every monitor, in physical pixels.</summary>
    public static (int X, int Y, int Width, int Height) VirtualScreen() => (
        GetSystemMetrics(SM_XVIRTUALSCREEN),
        GetSystemMetrics(SM_YVIRTUALSCREEN),
        GetSystemMetrics(SM_CXVIRTUALSCREEN),
        GetSystemMetrics(SM_CYVIRTUALSCREEN));

    /// <summary>Place a window by physical pixels, bypassing WPF's DIP layout
    /// so one overlay covers monitors that scale differently.</summary>
    public static void PlacePhysical(IntPtr hwnd, int x, int y, int width, int height) =>
        SetWindowPos(hwnd, HWND_TOPMOST, x, y, width, height, SWP_SHOWWINDOW | SWP_NOACTIVATE);

    /// <summary>Dark title bar, so the window frame matches the app.
    /// Silently does nothing on builds that predate the attribute.</summary>
    public static void UseDarkTitleBar(IntPtr hwnd)
    {
        var on = 1;
        try { DwmSetWindowAttribute(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, ref on, sizeof(int)); }
        catch (DllNotFoundException) { /* pre-Vista shell; nothing to do */ }
        catch (EntryPointNotFoundException) { /* older dwmapi */ }
    }
}
