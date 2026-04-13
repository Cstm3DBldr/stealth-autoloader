stealth-autoloader

install Command
wget -O - https://raw.githubusercontent.com/Cstm3DBldr/stealth-autoloader/main/install.sh | bash

After the command finishes, Add this one line to printer.cfg
[include stealth-autoloader/*.cfg]

Then Save And Restart Klipper

To Restore Printer
Uninstall Command

cd ~/stealth-autoloader
./install.sh --uninstall
rm -rf ~/stealth-autoloader

Usable Commands
FILAMENT_LOAD
FILAMENT_UNLOAD
BUFFER_CONTROL_ON   (placeholder - buffer auto control coming next)

You can edit stealth-autoloader/macros.cfg and stealth-autoloader/hardware.cfg directly in the web GUI.

Next Steps (when ready)
- Add buffer sensors (tension + compression switch)
- Auto tension loop in Python
- Copy-paste for all 6 toolheads

Simple add-on for Stealth Changer - no core changes.
