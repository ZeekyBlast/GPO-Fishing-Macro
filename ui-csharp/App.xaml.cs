using System;
using System.IO;
using System.Windows;
using System.Windows.Threading;

namespace GpoMacro;

public partial class App : Application
{
    protected override void OnStartup(StartupEventArgs e)
    {
        // A dead shell must not leave a headless Python holding the mouse
        // button down, so an unhandled fault is reported and then torn down
        // through the normal shutdown path.
        DispatcherUnhandledException += OnDispatcherException;
        base.OnStartup(e);
    }

    private void OnDispatcherException(object sender, DispatcherUnhandledExceptionEventArgs e)
    {
        var text = e.Exception.ToString();
        try { File.WriteAllText("ui-crash.log", text); } catch { /* nothing to do */ }
        MessageBox.Show(
            "The interface hit an error and will close. The macro has been stopped.\n\n" +
            e.Exception.Message + "\n\nDetails saved to ui-crash.log.",
            "GPO Fishing Macro", MessageBoxButton.OK, MessageBoxImage.Error);
        e.Handled = true;
        Shutdown(1);
    }
}
