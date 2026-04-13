install Command

wget -O - https://raw.githubusercontent.com/Cstm3DBldr/stealth-autoloader/main/install.sh | bash

After the command finishes, Add this one line to printer.cfg 
[include stealth-autoloader.cfg]

Then Save And Restart Klipper

To Restore Printer
Uninstall Command

cd ~/stealth-autoloader
./install.sh --uninstall
rm -rf ~/stealth-autoloader


Usable Commands 
FILAMENT_LOAD
FILAMENT_UNLOAD
