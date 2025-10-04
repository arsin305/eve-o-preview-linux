# 🛰️ eve-o-preview-linux

**Live thumbnail previews for EVE Online multiboxing on Linux.**  
Click to focus clients, drag to reposition, and zoom on hover.  
Features customizable borders, character overlays, and real-time updates.  
Works with both Steam and native clients on X11 — perfect for managing multiple EVE accounts efficiently.

![Python Version](https://img.shields.io/badge/python-3.8+-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Platform](https://img.shields.io/badge/platform-Linux%20X11-orange.svg)

---


## ✨ Features

- **Live Thumbnails:** Real-time preview of all EVE Online clients  
- **Click to Focus:** Single click to bring any client to focus  
- **Drag to Reposition:** Move thumbnails easily with your mouse  
- **Zoom on Hover:** Thumbnails enlarge when hovered  
- **Active Client Highlighting:** Customizable colored border  
- **Character Name Overlay:** Shows character names on each thumbnail  
- **Steam & Native Support:** Compatible with both Steam and native EVE clients  
- **Persistent Positions:** Saves thumbnail layout between sessions  
- **Customizable Settings:** Adjust size, opacity, refresh rate, and more  

---

## 🖥️ System Requirements

### Required
- **OS:** Linux with X11 display server  
- **Python:** 3.8+  
- **Display Server:** X11 (Wayland not supported — see troubleshooting)  
- **Desktop Environment:** Any (GNOME, KDE, XFCE, etc.)

### Tested Configuration
Developed and tested on:
- Fedora Linux 42 (Workstation Edition)
- Kernel 6.16.9-200.fc42.x86_64
- GNOME 48.4 on Wayland (requires X11 session)
- AMD Ryzen 7 5800X + Radeon RX 7900 XTX
- 64 GB RAM

---

## 📸 Screenshots

| Description | Image |
|--------------|-------|
| **Main Control Window**<br>Displays active clients, session type, and backend info. | ![Main Window](screenshots/01-main-window.png) |
| **Display Settings Tab**<br>Adjust thumbnail size, opacity, and active border color. | ![Display Settings](screenshots/02-settings-display.png) |
| **Behavior Settings Tab**<br>Configure always-on-top, overlays, zoom, and refresh rate. | ![Behavior Settings](screenshots/03-settings-behavior.png) |
| **Switching Clients**<br>Click a thumbnail to instantly focus — red border shows active window. | ![Switch Focus](screenshots/04-thumbnail-switching.png) |
| **Active Client Highlighting**<br>Example showing red border around the active client. | ![Highlight](screenshots/05-active-client-highlight.png) |

---

## 🎥 Demo Video

▶️ [Watch on Streamable](https://streamable.com/rss05k)

---

## 🧩 Python Dependencies

```bash
python3-gi
gir1.2-gtk-3.0
gir1.2-wnck-3.0
gir1.2-gdkx11-3.0
```

---

## 📦 Installation

### Fedora / RHEL (tested)
```bash
sudo dnf install python3 gtk3 libwnck3
wget https://raw.githubusercontent.com/arsin305/eve-o-preview-linux/main/eve_preview_enhanced.py
chmod +x eve_preview_enhanced.py
./eve_preview_enhanced.py
```

### Ubuntu / Debian
```bash
sudo apt update
sudo apt install python3 python3-gi gir1.2-gtk-3.0 gir1.2-wnck-3.0
wget https://raw.githubusercontent.com/arsin305/eve-o-preview-linux/main/eve_preview_enhanced.py
chmod +x eve_preview_enhanced.py
./eve_preview_enhanced.py
```

### Arch Linux
```bash
sudo pacman -S python gtk3 libwnck3
wget https://raw.githubusercontent.com/arsin305/eve-o-preview-linux/main/eve_preview_enhanced.py
chmod +x eve_preview_enhanced.py
./eve_preview_enhanced.py
```

---

## 🚀 Usage

### Basic Usage
1. Start your EVE Online clients (Steam or native)
2. Run the script:
   ```bash
   ./eve_preview_enhanced.py
   ```
   Thumbnails will appear automatically for each client.

### Controls
| Action | Description |
|---------|-------------|
| **Left Click** | Focus/activate client |
| **Left Drag** | Move thumbnail |
| **Right Drag** | Alternate move method |
| **Ctrl + Left Click** | Minimize client |
| **Hover** | Zoom thumbnail (if enabled) |

---

## ⚙️ Configuration

Settings are stored at:
```
~/.config/eve-o-preview-linux/config.json
```

Example:
```json
{
  "thumbnail_width": 320,
  "thumbnail_height": 200,
  "opacity": 0.95,
  "always_on_top": true,
  "hide_active_client": false,
  "zoom_on_hover": true,
  "zoom_factor": 1.25,
  "show_overlay": true,
  "refresh_fps": 10,
  "active_border_color": "#00FF00",
  "thumbnail_positions": {}
}
```

Use the built-in settings dialog to adjust appearance and behavior.

---

## 🐛 Troubleshooting

### Wayland (GNOME/Fedora)
This tool requires **X11** to function.  
**Option 1 (recommended):**
1. Log out of GNOME  
2. At login screen → click ⚙️ → select **“GNOME on Xorg”**  
3. Log in and run the script

**Option 2:** Force X11 backend  
```bash
GDK_BACKEND=x11 ./eve_preview_enhanced.py
```

Check your session type:
```bash
echo $XDG_SESSION_TYPE  # should show "x11"
```

### Common Issues

**Thumbnails not appearing**
- Verify you’re on X11 (`echo $XDG_SESSION_TYPE`)
- Ensure EVE clients are running (`ps aux | grep exefile`)
- Check window titles start with `EVE - `

**Steam clients not detected**
- The script looks for `exefile.exe` in process list

**Black thumbnails**
- Disable compositing in your DE
- Ensure Mesa/AMDGPU drivers are updated
- Try lower FPS (10 FPS recommended)

**SELinux (Fedora/RHEL)**
```bash
getenforce
sudo setenforce 0   # test in permissive mode
```
If this fixes it, create a custom policy or permanently disable enforcement for this script.

---

## 🤝 Contributing

Pull requests welcome!  
For major changes, please [open an issue](https://github.com/arsin305/eve-o-preview-linux/issues) first.

```bash
git clone https://github.com/arsin305/eve-o-preview-linux.git
cd eve-o-preview-linux
./eve_preview_enhanced.py
```

---

## 📝 Known Issues
- Wayland: not supported (use X11 session)
- Some compositors may show black thumbnails
- High DPI scaling may need manual adjustment
- Multi-monitor setups default thumbnails to primary screen

---

## 🎮 Performance Notes

**Recommended Settings**
- Refresh rate: 10 FPS (smooth & light)
- Thumbnail size: 320×200
- Opacity: 0.95

**High-end systems (e.g. Ryzen 7 5800X + RX 7900 XTX)**  
Use 15–25 FPS and larger thumbnails (400×250+) for smoother display.

**Low-end systems**
- 10 FPS  
- Smaller thumbnails (280×175)  
- Disable zoom on hover

---

## ⚖️ Legal & Compliance

**Third-Party Tool Disclaimer:**  
This is an unofficial, community-created tool and is **not affiliated with, endorsed by, or supported by CCP Games**.  
EVE Online™ is a registered trademark of CCP hf.

**Personal Project:**  
Originally developed for personal use while multiboxing EVE Online on Linux.  
Shared publicly for educational and usability purposes — support is best-effort.

**User Responsibility:**  
By using this tool, you acknowledge that:
- You are responsible for ensuring compliance with [CCP’s EULA](https://community.eveonline.com/support/policies/eve-eula-en/) and [Third-Party Policy](https://support.eveonline.com/hc/en-us/articles/202732751-Third-Party-Applications-and-Other-Software)  
- Provided “as-is” without warranty  
- Developer(s) are not liable for any consequences from use  

**What This Tool Does:**  
EVE-O Preview is a passive observation tool that creates visual thumbnails of EVE Online client windows.  
It does *not*:
- Modify game files or memory  
- Inject code into the client  
- Automate gameplay  
- Provide unfair advantages  
- Send or intercept network traffic  

---

## 🔗 Links

- [EVE Online](https://www.eveonline.com/)
- [Original EVE-O Preview (Windows)](https://github.com/Phrynohyas/eve-o-preview)
- [Report Issues](https://github.com/arsin305/eve-o-preview-linux/issues)

---

## 📞 Support

If you encounter issues:
1. Review the troubleshooting section above  
2. Check open [GitHub issues](https://github.com/arsin305/eve-o-preview-linux/issues)  
3. Include when reporting:
   - Linux distro and version  
   - Output of `echo $XDG_SESSION_TYPE`  
   - Error messages from terminal  
   - Screenshot (if applicable)

---

## 📜 License

Licensed under the **MIT License** — see [LICENSE](LICENSE).

---

## 🙏 Acknowledgments

- Inspired by [EVE-O Preview for Windows](https://github.com/Phrynohyas/eve-o-preview)  
- EVE Online™ is a trademark of CCP hf.  
- This tool is not affiliated with or endorsed by CCP Games.

---

##🧠 Future Move to PipeWire

I’m not a professional coder — just a Linux tinkerer still learning and experimenting.
This project is a personal passion project that keeps evolving as I discover new tools and APIs.

A long-term goal for Fedora 43+ is to explore PipeWire’s window capture and frame-sharing APIs as a potential enhancement.
The goal is to:

Capture frames from running EVE Online clients more efficiently

Potentially bypass focus-related throttling for smoother thumbnails

Add Wayland session support without depending on X11

This feature will be tested first on Fedora 43 with PipeWire 1.2+ once the new API stabilizes.
If successful, it should bring smoother performance and better Wayland compatibility — a big step forward for Linux multiboxers on modern desktops.
