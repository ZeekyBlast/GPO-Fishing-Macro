using System;
using System.IO;
using System.Threading;
using System.Windows;
using System.Windows.Threading;

namespace GpoMacro;

public partial class App : Application
{
    // Two copies would mean two engines fighting over the same mouse button,
    // which is worse than either one alone. The installer looks for this same
    // name to tell whether it is about to overwrite a running copy.
    private const string InstanceMutexName = "GpoFishingMacroRunning";
    private Mutex? _instanceMutex;

    protected override void OnStartup(StartupEventArgs e)
    {
        _instanceMutex = new Mutex(initiallyOwned: true, InstanceMutexName, out var isFirst);
        if (!isFirst)
        {
            MessageBox.Show("GPO Fishing Macro is already running.",
                            "GPO Fishing Macro", MessageBoxButton.OK, MessageBoxImage.Information);
            Shutdown();
            return;
        }

        // A dead shell must not leave a headless Python holding the mouse
        // button down, so an unhandled fault is reported and then torn down
        // through the normal shutdown path.
        DispatcherUnhandledException += OnDispatcherException;
        base.OnStartup(e);
    }

    protected override void OnExit(ExitEventArgs e)
    {
        _instanceMutex?.Dispose();
        base.OnExit(e);
    }

    // A fault during layout re-throws on every subsequent layout pass, so the
    // first version of this handler produced one dialog per pass: a wall of
    // dozens of message boxes stacked across the screen. Report once.
    private bool _reportedFault;

    private void OnDispatcherException(object sender, DispatcherUnhandledExceptionEventArgs e)
    {
        e.Handled = true;
        if (_reportedFault) return;
        _reportedFault = true;

        var text = e.Exception.ToString();
        try
        {
            File.WriteAllText(
                Path.Combine(AppContext.BaseDirectory, "ui-crash.log"), text);
        }
        catch (Exception)
        {
            // Read-only install folder. The dialog still carries the message.
        }

        MessageBox.Show(
            "The interface hit an error and will close. The macro has been stopped.\n\n" +
            e.Exception.Message + "\n\nDetails saved to ui-crash.log.",
            "GPO Fishing Macro", MessageBoxButton.OK, MessageBoxImage.Error);
        Shutdown(1);
    }
}
