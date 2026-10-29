/* NativeWindowProbe.cs
 *
 * Copyright (C) 2026 Paul Boersma
 *
 * This code is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 3 of the License, or (at
 * your option) any later version.
 *
 * This code is distributed in the hope that it will be useful, but
 * WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this work. If not, see <http://www.gnu.org/licenses/>.
 */

/*
	Win32 probe used by tools/verify-native-appearance.ps1. It reads what the window
	manager and the GDI actually do with a running editor (window class, layered style,
	alpha byte, menu-bar rectangles, panel controls, clipboard bytes) and it sends real
	mouse and keyboard input, so the checks describe the user's path rather than the source.
*/

using System;
using System.Collections;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;

public static class N {
	[StructLayout(LayoutKind.Sequential)]
	public struct RECT { public int left; public int top; public int right; public int bottom; }
	[StructLayout(LayoutKind.Sequential)]
	public struct POINT { public int x; public int y; }

	public delegate bool EnumProc (IntPtr handle, IntPtr parameter);

	[DllImport("user32.dll")] private static extern bool EnumWindows (EnumProc callback, IntPtr parameter);
	[DllImport("user32.dll")] private static extern bool EnumChildWindows (IntPtr parent, EnumProc callback, IntPtr parameter);
	[DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId (IntPtr window, out uint processId);
	[DllImport("user32.dll", CharSet=CharSet.Unicode)] private static extern int GetClassName (IntPtr window, StringBuilder buffer, int maximum);
	[DllImport("user32.dll", CharSet=CharSet.Unicode)] private static extern int GetWindowText (IntPtr window, StringBuilder buffer, int maximum);
	[DllImport("user32.dll")] private static extern bool IsWindowVisible (IntPtr window);
	[DllImport("user32.dll")] private static extern bool IsWindow (IntPtr window);
	[DllImport("user32.dll")] private static extern bool GetWindowRect (IntPtr window, out RECT rectangle);
	[DllImport("user32.dll")] private static extern bool GetClientRect (IntPtr window, out RECT rectangle);
	[DllImport("user32.dll")] private static extern bool ClientToScreen (IntPtr window, ref POINT point);
	[DllImport("user32.dll")] private static extern IntPtr GetWindowLongPtr (IntPtr window, int index);
	[DllImport("user32.dll")] private static extern bool SetForegroundWindow (IntPtr window);
	[DllImport("user32.dll")] private static extern bool AttachThreadInput (uint source, uint target, bool attach);
	[DllImport("kernel32.dll")] private static extern uint GetCurrentThreadId ();
	[DllImport("user32.dll")] private static extern IntPtr GetForegroundWindow ();
	[DllImport("user32.dll")] private static extern IntPtr SetFocus (IntPtr window);
	[DllImport("user32.dll")] private static extern IntPtr GetParent (IntPtr window);
	[DllImport("user32.dll")] private static extern IntPtr GetWindow (IntPtr window, uint command);
	[DllImport("user32.dll")] private static extern IntPtr GetMenu (IntPtr window);
	[DllImport("user32.dll")] private static extern int GetMenuItemCount (IntPtr menu);
	[DllImport("user32.dll")] private static extern IntPtr GetSubMenu (IntPtr menu, int position);
	[DllImport("user32.dll")] private static extern uint GetMenuItemID (IntPtr menu, int position);
	[DllImport("user32.dll", CharSet=CharSet.Unicode)] private static extern int GetMenuString (IntPtr menu, uint identifier, StringBuilder buffer, int maximum, uint flags);
	[DllImport("user32.dll")] private static extern bool GetMenuItemRect (IntPtr window, IntPtr menu, uint position, out RECT rectangle);
	[DllImport("user32.dll")] private static extern uint GetDpiForWindow (IntPtr window);
	[DllImport("user32.dll")] private static extern bool GetLayeredWindowAttributes (IntPtr window, out uint colourKey, out byte alpha, out uint flags);
	[DllImport("user32.dll")] private static extern IntPtr SendMessage (IntPtr window, uint message, IntPtr wParam, IntPtr lParam);
	[DllImport("user32.dll")] private static extern bool PostMessage (IntPtr window, uint message, IntPtr wParam, IntPtr lParam);
	[DllImport("user32.dll")] private static extern bool SetCursorPos (int x, int y);
	[DllImport("user32.dll")] private static extern bool GetCursorPos (out POINT point);
	[DllImport("user32.dll")] private static extern void mouse_event (uint flags, uint dx, uint dy, uint data, UIntPtr extra);
	[DllImport("user32.dll")] private static extern void keybd_event (byte virtualKey, byte scanCode, uint flags, UIntPtr extra);
	[DllImport("user32.dll")] private static extern bool SetWindowPos (IntPtr window, IntPtr insertAfter, int x, int y, int width, int height, uint flags);
	[DllImport("user32.dll")] private static extern bool PrintWindow (IntPtr window, IntPtr deviceContext, uint flags);
	[DllImport("user32.dll")] private static extern IntPtr GetDC (IntPtr window);
	[DllImport("user32.dll")] private static extern int ReleaseDC (IntPtr window, IntPtr deviceContext);
	[DllImport("gdi32.dll")] private static extern IntPtr CreateCompatibleDC (IntPtr deviceContext);
	[DllImport("gdi32.dll")] private static extern IntPtr CreateCompatibleBitmap (IntPtr deviceContext, int width, int height);
	[DllImport("gdi32.dll")] private static extern IntPtr SelectObject (IntPtr deviceContext, IntPtr item);
	[DllImport("gdi32.dll")] private static extern bool DeleteObject (IntPtr item);
	[DllImport("gdi32.dll")] private static extern bool DeleteDC (IntPtr deviceContext);
	[DllImport("gdi32.dll")] private static extern bool BitBlt (IntPtr destination, int x, int y, int width, int height,
			IntPtr source, int sourceX, int sourceY, uint operation);
	[DllImport("gdi32.dll")] private static extern void GdiFlush ();
	[DllImport("user32.dll")] private static extern bool OpenClipboard (IntPtr window);
	[DllImport("user32.dll")] private static extern bool CloseClipboard ();
	[DllImport("user32.dll")] private static extern bool EmptyClipboard ();
	[DllImport("user32.dll")] private static extern IntPtr GetClipboardData (uint format);
	[DllImport("user32.dll")] private static extern uint CountClipboardFormats ();
	[DllImport("user32.dll")] private static extern uint EnumClipboardFormats (uint format);
	[DllImport("kernel32.dll")] private static extern IntPtr GlobalSize (IntPtr memory);
	[DllImport("kernel32.dll")] private static extern IntPtr GlobalLock (IntPtr memory);
	[DllImport("kernel32.dll")] private static extern bool GlobalUnlock (IntPtr memory);
	[DllImport("user32.dll")] private static extern bool GetGUIThreadInfo (uint threadId, ref GUITHREADINFO information);

	[StructLayout(LayoutKind.Sequential)]
	private struct GUITHREADINFO {
		public int cbSize; public int flags;
		public IntPtr hwndActive; public IntPtr hwndFocus; public IntPtr hwndCapture;
		public IntPtr hwndMenuOwner; public IntPtr hwndMoveSize; public IntPtr hwndCaret;
		public RECT caretRectangle;
	}

	private const int GWL_STYLE = (-16);
	private const int GWL_EXSTYLE = (-20);
	private const int WS_EX_LAYERED = 0x80000;
	private const int WS_EX_NOACTIVATE = 0x8000000;
	private const int BS_OWNERDRAW = 0xB;
	private const uint LWA_ALPHA = 0x2;
	private const uint MF_BYCOMMAND = 0x0;
	private const uint MF_BYPOSITION = 0x400;
	private const int WM_COMMAND = 0x0111;
	private const int WM_KEYDOWN = 0x0100;
	private const int WM_KEYUP = 0x0101;
	private const int TBM_GETRANGEMIN = 0x0401;
	private const int TBM_GETRANGEMAX = 0x0402;
	private const int TBM_GETPOS = 0x0400;
	private const int CF_UNICODETEXT = 13;
	private const int CF_DIB = 8;
	private const int CF_DIBV5 = 17;

	public static IntPtr[] TopLevel (long processId) {
		List<IntPtr> windows = new List<IntPtr> ();
		EnumWindows (delegate (IntPtr window, IntPtr parameter) {
			uint windowProcessId;
			GetWindowThreadProcessId (window, out windowProcessId);
			if (windowProcessId == processId && IsWindowVisible (window))
				windows.Add (window);
			return true;
		}, IntPtr.Zero);
		return windows.ToArray ();
	}

	public static IntPtr[] Children (IntPtr parent) {
		List<IntPtr> windows = new List<IntPtr> ();
		EnumChildWindows (parent, delegate (IntPtr window, IntPtr parameter) {
			windows.Add (window);
			return true;
		}, IntPtr.Zero);
		return windows.ToArray ();
	}

	public static bool Alive (IntPtr window) { return window != IntPtr.Zero && IsWindow (window); }

	[DllImport("user32.dll")] private static extern int GetDlgCtrlID (IntPtr window);
	/* The control identifier the panel gave each chip, slider and read-out. */
	public static int ControlId (IntPtr window) { return GetDlgCtrlID (window); }

	public static string Class (IntPtr window) {
		StringBuilder buffer = new StringBuilder (256);
		GetClassName (window, buffer, buffer.Capacity);
		return buffer.ToString ();
	}

	public static string Text (IntPtr window) {
		StringBuilder buffer = new StringBuilder (512);
		GetWindowText (window, buffer, buffer.Capacity);
		return buffer.ToString ();
	}

	public static IntPtr Parent (IntPtr window) { return GetParent (window); }

	private const int GWLP_HWNDPARENT = (-8);
	/* The owner window of a popup: the panel belongs to one editor window and to nobody else. */
	public static IntPtr Owner (IntPtr window) { return GetWindowLongPtr (window, GWLP_HWNDPARENT); }

	public static int[] Rect (IntPtr window) {
		RECT rectangle;
		if (! GetWindowRect (window, out rectangle)) return new int [] { 0, 0, 0, 0 };
		return new int [] { rectangle.left, rectangle.top, rectangle.right, rectangle.bottom };
	}

	/* The client rectangle in screen coordinates: what a screenshot of the drawing area covers. */
	public static int[] ClientRectOnScreen (IntPtr window) {
		RECT rectangle;
		if (! GetClientRect (window, out rectangle)) return new int [] { 0, 0, 0, 0 };
		POINT topLeft = new POINT (); topLeft.x = 0; topLeft.y = 0;
		ClientToScreen (window, ref topLeft);
		return new int [] { topLeft.x, topLeft.y, topLeft.x + rectangle.right, topLeft.y + rectangle.bottom };
	}

	public static long Style (IntPtr window) { return GetWindowLongPtr (window, GWL_STYLE).ToInt64 (); }
	public static long ExStyle (IntPtr window) { return GetWindowLongPtr (window, GWL_EXSTYLE).ToInt64 (); }
	public static bool IsLayered (IntPtr window) { return (ExStyle (window) & WS_EX_LAYERED) != 0; }
	public static bool DoesNotActivate (IntPtr window) { return (ExStyle (window) & WS_EX_NOACTIVATE) != 0; }
	public static bool IsOwnerDraw (IntPtr window) { return (Style (window) & 0xF) == BS_OWNERDRAW; }
	public static int Dpi (IntPtr window) { return (int) GetDpiForWindow (window); }

	/* -1 when the window is not layered; else the alpha byte that the system composites with. */
	public static int Alpha (IntPtr window) {
		uint colourKey; byte alpha; uint flags;
		if (! GetLayeredWindowAttributes (window, out colourKey, out alpha, out flags)) return -1;
		if ((flags & LWA_ALPHA) == 0) return -1;
		return (int) alpha;
	}

	public static IntPtr MenuOf (IntPtr window) { return GetMenu (window); }
	public static int MenuCount (IntPtr menu) { return menu == IntPtr.Zero ? -1 : GetMenuItemCount (menu); }

	public static string MenuStringAt (IntPtr menu, int position) {
		if (menu == IntPtr.Zero) return "";
		StringBuilder buffer = new StringBuilder (512);
		GetMenuString (menu, (uint) position, buffer, buffer.Capacity, MF_BYPOSITION);
		return buffer.ToString ();
	}

	public static uint MenuIdAt (IntPtr menu, int position) { return menu == IntPtr.Zero ? 0u : GetMenuItemID (menu, position); }
	public static IntPtr MenuSubAt (IntPtr menu, int position) { return menu == IntPtr.Zero ? IntPtr.Zero : GetSubMenu (menu, position); }

	/* The rectangle a menu-bar entry really occupies on the screen, as the editor's own panel anchor uses it. */
	public static int[] MenuRectOnScreen (IntPtr window, IntPtr menu, int position) {
		RECT rectangle;
		if (menu == IntPtr.Zero || ! GetMenuItemRect (window, menu, (uint) position, out rectangle))
			return new int [] { -1, -1, -1, -1 };
		return new int [] { rectangle.left, rectangle.top, rectangle.right, rectangle.bottom };
	}

	public static void Foreground (IntPtr window) { SetForegroundWindow (window); }

	/*
		A background caller is normally refused the foreground. Briefly pressing ALT is the
		documented way to lift that lock, so the same call works from an automated session.
	*/
	public static bool ForceForeground (IntPtr window) {
		if (GetForegroundWindow () == window) return true;
		SetForegroundWindow (window);
		if (GetForegroundWindow () == window) return true;
		keybd_event (0xA4, 0, 0, UIntPtr.Zero);
		keybd_event (0xA4, 0, 0x0002, UIntPtr.Zero);
		uint processId;
		uint foregroundThread = GetWindowThreadProcessId (GetForegroundWindow (), out processId);
		uint callerThread = GetCurrentThreadId ();
		bool attached = foregroundThread != callerThread && AttachThreadInput (callerThread, foregroundThread, true);
		try { SetForegroundWindow (window); }
		finally { if (attached) AttachThreadInput (callerThread, foregroundThread, false); }
		return GetForegroundWindow () == window;
	}

	public static IntPtr GetForeground () { return GetForegroundWindow (); }
	public static void Focus (IntPtr window) { SetFocus (window); }

	/*
		Which control really has the keyboard in a running process. Every thread of that process is
		asked, because GetGUIThreadInfo reports the focus of one thread and Praat spreads its windows
		over a few. This is the only way to see the focus from outside: SetFocus across processes does
		nothing, so the answer has to be read rather than set.
	*/
	public static IntPtr FocusedInProcess (int processId) {
		System.Diagnostics.Process process;
		try { process = System.Diagnostics.Process.GetProcessById (processId); }
		catch (ArgumentException) { return IntPtr.Zero; }
		try {
			foreach (System.Diagnostics.ProcessThread thread in process.Threads) {
				GUITHREADINFO information = new GUITHREADINFO ();
				information.cbSize = Marshal.SizeOf (typeof (GUITHREADINFO));
				if (! GetGUIThreadInfo ((uint) thread.Id, ref information)) continue;
				if (information.hwndFocus != IntPtr.Zero) return information.hwndFocus;
			}
		} finally {
			foreach (System.Diagnostics.ProcessThread thread in process.Threads) thread.Dispose ();
			process.Dispose ();
		}
		return IntPtr.Zero;
	}

	public static bool Visible (IntPtr window) { return IsWindowVisible (window); }

	/* SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE, with HWND_TOP: the window comes forward unchanged. */
	public static void BringToTop (IntPtr window) {
		SetWindowPos (window, (IntPtr) 0, 0, 0, 0, 0, 0x0002 | 0x0001 | 0x0010);
	}

	/*
		Placement for the screen captures: the window has to be where the capture expects it and
		nothing may lie on top of it, so the move and the z order happen in one call.
	*/
	public static bool Place (IntPtr window, int x, int y, int width, int height) {
		return SetWindowPos (window, (IntPtr) 0, x, y, width, height, 0x0010);   // SWP_NOACTIVATE
	}

	public static IntPtr Send (IntPtr window, uint message, long wParam, long lParam) {
		return SendMessage (window, message, (IntPtr) wParam, (IntPtr) lParam);
	}
	public static IntPtr SendSimple (IntPtr window, uint message, int wParam) {
		return SendMessage (window, message, (IntPtr) wParam, IntPtr.Zero);
	}
	public static void SendCommand (IntPtr window, int identifier) {
		// A modal form runs its own message loop; a synchronous sender cannot then operate that form.
		if (! PostMessage (window, WM_COMMAND, (IntPtr) identifier, IntPtr.Zero))
			throw new InvalidOperationException ("Could not post the native menu command.");
	}

	public static int TrackbarMinimum (IntPtr trackbar) { return SendSimple (trackbar, TBM_GETRANGEMIN, 0).ToInt32 (); }
	public static int TrackbarMaximum (IntPtr trackbar) { return SendSimple (trackbar, TBM_GETRANGEMAX, 0).ToInt32 (); }
	public static int TrackbarPosition (IntPtr trackbar) { return SendSimple (trackbar, TBM_GETPOS, 0).ToInt32 (); }

	/*
		No trackbar query that carries a pointer is sent from here: the common-control messages that
		write a rectangle into the caller's buffer are not marshalled across processes, so sending one
		makes the control write into whatever that address means in the editor and the editor dies in
		comctl32. The handle of another process's slider is therefore read from the pixels the desktop
		compositor painted, which is also the only version a user can see.
	*/
	public static int[] PaintedHandle (IntPtr slider) {
		int [] client = ClientRectOnScreen (slider);
		int width = client [2] - client [0], height = client [3] - client [1];
		if (width < 16 || height < 8) return null;
		int row = (client [1] + client [3]) / 2;
		int [] kind = new int [width];
		IntPtr screen = GetDC (IntPtr.Zero);
		IntPtr deviceContext = CreateCompatibleDC (screen);
		IntPtr bitmap = CreateCompatibleBitmap (screen, width, 1);
		IntPtr previous = SelectObject (deviceContext, bitmap);
		BitBlt (deviceContext, 0, 0, width, 1, screen, client [0], row, 0x00CC0020);
		GdiFlush ();
		using (Bitmap image = (Bitmap) Image.FromHbitmap (bitmap)) {
			for (int column = 0; column < width; column ++) {
				Color colour = image.GetPixel (column, 0);
				int brightest = Math.Max (colour.R, Math.Max (colour.G, colour.B));
				int darkest = Math.Min (colour.R, Math.Min (colour.G, colour.B));
				kind [column] = (darkest >= 246) ? 1 : (brightest <= 150 ? 2 : 0);
			}
		}
		SelectObject (deviceContext, previous);
		DeleteObject (bitmap);
		DeleteDC (deviceContext);
		ReleaseDC (IntPtr.Zero, screen);
		/*
			Along that row the circle is the only white stretch that has dark pixels on both sides: the
			groove to its right is grey, the filled part to its left is dark, and the panel around the
			groove is white but reaches the ends of the row instead of sitting between dark columns.
		*/
		int bestStart = -1, bestEnd = -1;
		int start = -1;
		for (int column = 0; column <= width; column ++) {
			bool white = column < width && kind [column] == 1;
			if (white && start < 0) start = column;
			if (! white && start >= 0) {
				int length = column - start;
				bool darkOnLeft = false, darkOnRight = false;
				for (int step = 1; step <= 3; step ++) {
					if (start - step >= 0 && kind [start - step] == 2) darkOnLeft = true;
					if (column - 1 + step < width && kind [column - 1 + step] == 2) darkOnRight = true;
				}
				if (length >= 5 && darkOnLeft && darkOnRight && length > (bestEnd - bestStart)) {
					bestStart = start; bestEnd = column;
				}
				start = -1;
			}
		}
		if (bestStart < 0) return null;
		int edge = bestEnd;
		while (edge < width && kind [edge] == 2) edge ++;
		int centre = client [0] + (bestStart + bestEnd) / 2;
		return new int [] { centre, client [0] + edge - 1 - centre, bestEnd - bestStart };
	}

	public static int ProcessIdOf (IntPtr window) {
		uint processId;
		GetWindowThreadProcessId (window, out processId);
		return (int) processId;
	}
	public static int ThreadIdOf (IntPtr window) {
		uint processId;
		return (int) GetWindowThreadProcessId (window, out processId);
	}

	/*
		The window that owns the pointer capture in that process. While a drag is held this is the
		control that receives every move, however far outside its row or its panel the pointer goes,
		so "the pointer left the panel" only means something when the capture is read back.
	*/
	public static IntPtr CapturedInProcess (int processId) {
		System.Diagnostics.Process process;
		try { process = System.Diagnostics.Process.GetProcessById (processId); }
		catch (ArgumentException) { return IntPtr.Zero; }
		try {
			foreach (System.Diagnostics.ProcessThread thread in process.Threads) {
				GUITHREADINFO information = new GUITHREADINFO ();
				information.cbSize = Marshal.SizeOf (typeof (GUITHREADINFO));
				if (! GetGUIThreadInfo ((uint) thread.Id, ref information)) continue;
				if (information.hwndCapture != IntPtr.Zero) return information.hwndCapture;
			}
		} finally {
			foreach (System.Diagnostics.ProcessThread thread in process.Threads) thread.Dispose ();
			process.Dispose ();
		}
		return IntPtr.Zero;
	}

	public static int[] Cursor () {
		POINT point;
		if (! GetCursorPos (out point)) return new int [] { -1, -1 };
		return new int [] { point.x, point.y };
	}

	/*
		The three steps of a press kept apart, so the script can look at the window manager between
		them: where the pointer is, who holds the capture, what the control reports. Drag () cannot do
		that, because it releases the button before the caller gets a chance to look.
	*/
	public static void Press (int x, int y) {
		SetCursorPos (x, y);
		mouse_event (0x0002, 0, 0, 0, UIntPtr.Zero);   // LEFTDOWN
		System.Threading.Thread.Sleep (40);
	}
	public static void MovePointer (int x, int y) {
		SetCursorPos (x, y);
		System.Threading.Thread.Sleep (20);
	}
	public static void ReleasePointer (int x, int y) {
		SetCursorPos (x, y);
		mouse_event (0x0004, 0, 0, 0, UIntPtr.Zero);   // LEFTUP
		System.Threading.Thread.Sleep (40);
	}

	public static void Click (int x, int y) {
		SetCursorPos (x, y);
		mouse_event (0x0002, 0, 0, 0, UIntPtr.Zero);   // LEFTDOWN
		System.Threading.Thread.Sleep (40);
		mouse_event (0x0004, 0, 0, 0, UIntPtr.Zero);   // LEFTUP
		System.Threading.Thread.Sleep (40);
	}

	/* A real drag: button down on the start point, intermediate moves, button up on the end point. */
	public static void Drag (int fromX, int fromY, int toX, int toY, int steps) {
		SetCursorPos (fromX, fromY);
		mouse_event (0x0002, 0, 0, 0, UIntPtr.Zero);
		System.Threading.Thread.Sleep (40);
		for (int step = 1; step <= steps; step ++) {
			SetCursorPos (fromX + (toX - fromX) * step / steps, fromY + (toY - fromY) * step / steps);
			System.Threading.Thread.Sleep (12);
		}
		mouse_event (0x0004, 0, 0, 0, UIntPtr.Zero);
		System.Threading.Thread.Sleep (40);
	}

	public static void KeyPress (int virtualKey) {
		keybd_event ((byte) virtualKey, 0, 0, UIntPtr.Zero);
		System.Threading.Thread.Sleep (25);
		keybd_event ((byte) virtualKey, 0, 0x0002, UIntPtr.Zero);
		System.Threading.Thread.Sleep (25);
	}

	public static bool SaveWindowPng (IntPtr window, string path) {
		int [] client = ClientRectOnScreen (window);
		int width = client [2] - client [0], height = client [3] - client [1];
		if (width <= 0 || height <= 0) return false;
		IntPtr screen = GetDC (IntPtr.Zero);
		IntPtr deviceContext = CreateCompatibleDC (screen);
		IntPtr bitmap = CreateCompatibleBitmap (screen, width, height);
		IntPtr previous = SelectObject (deviceContext, bitmap);
		bool drawn = PrintWindow (window, deviceContext, 2);   // PW_RENDERFULLCONTENT
		GdiFlush ();
		bool saved = false;
		if (drawn) {
			using (Image image = Image.FromHbitmap (bitmap))
				image.Save (path, ImageFormat.Png);
			saved = File.Exists (path);
		}
		SelectObject (deviceContext, previous);
		DeleteObject (bitmap);
		DeleteDC (deviceContext);
		ReleaseDC (IntPtr.Zero, screen);
		return saved;
	}

	/* Screen capture: the only view that shows what the desktop compositor really paints. */
	public static bool SaveScreenPng (int x, int y, int width, int height, string path) {
		if (width <= 0 || height <= 0) return false;
		IntPtr screen = GetDC (IntPtr.Zero);
		IntPtr deviceContext = CreateCompatibleDC (screen);
		IntPtr bitmap = CreateCompatibleBitmap (screen, width, height);
		IntPtr previous = SelectObject (deviceContext, bitmap);
		BitBlt (deviceContext, 0, 0, width, height, screen, x, y, 0x00CC0020);   // SRCCOPY
		GdiFlush ();
		using (Image image = Image.FromHbitmap (bitmap))
			image.Save (path, ImageFormat.Png);
		SelectObject (deviceContext, previous);
		DeleteObject (bitmap);
		DeleteDC (deviceContext);
		ReleaseDC (IntPtr.Zero, screen);
		return File.Exists (path);
	}

	private static Bitmap Load (string path) {
		using (Bitmap source = new Bitmap (path))
			return source.Clone (new Rectangle (0, 0, source.Width, source.Height), PixelFormat.Format32bppArgb);
	}

	/*
		Colour statistics for a captured image. colours is a flat r,g,b list; every entry is
		counted with a per-channel tolerance, plus the pure-white and the non-opaque counts.
	*/
	public static Hashtable AnalysePng (string path, int [] colours, int tolerance) {
		Hashtable result = new Hashtable ();
		if (! File.Exists (path)) { result ["error"] = "missing"; return result; }
		Bitmap image = Load (path);
		try {
			int width = image.Width, height = image.Height;
			Rectangle area = new Rectangle (0, 0, width, height);
			BitmapData data = image.LockBits (area, ImageLockMode.ReadOnly, PixelFormat.Format32bppArgb);
			int bytes = Math.Abs (data.Stride) * image.Height;
			byte [] buffer = new byte [bytes];
			Marshal.Copy (data.Scan0, buffer, 0, buffer.Length);
			image.UnlockBits (data);
			int [] counts = new int [colours.Length / 3];
			long white = 0, nonOpaque = 0, minRed = 255, minGreen = 255, minBlue = 255;
			for (int pixel = 0; pixel < width * height; pixel ++) {
				int offset = (pixel / width) * data.Stride + (pixel % width) * 4;
				byte blue = buffer [offset], green = buffer [offset + 1], red = buffer [offset + 2], alpha = buffer [offset + 3];
				if (alpha != 255) nonOpaque ++;
				if (red == 255 && green == 255 && blue == 255) white ++;
				if (red < minRed) minRed = red;
				if (green < minGreen) minGreen = green;
				if (blue < minBlue) minBlue = blue;
				for (int colour = 0; colour < counts.Length; colour ++) {
					int dr = Math.Abs (red - colours [colour * 3]), dg = Math.Abs (green - colours [colour * 3 + 1]),
							db = Math.Abs (blue - colours [colour * 3 + 2]);
					if (dr <= tolerance && dg <= tolerance && db <= tolerance) counts [colour] ++;
				}
			}
			result ["width"] = width; result ["height"] = height;
			result ["counts"] = counts; result ["white"] = white;
			result ["nonOpaque"] = nonOpaque;
			result ["darkest"] = new int [] { (int) minRed, (int) minGreen, (int) minBlue };
			result ["sha256"] = Sha256OfFile (path);
		} finally { image.Dispose (); }
		return result;
	}

	/*
		Run lengths of one colour, measured perpendicular to the stroke (axis 0 follows the
		x direction, axis 1 the y direction). Gives the drawn thickness in device pixels.
	 */
	public static Hashtable RunStats (string path, int red, int green, int blue, int tolerance, int axis) {
		Hashtable result = new Hashtable ();
		if (! File.Exists (path)) { result ["error"] = "missing"; return result; }
		Bitmap image = Load (path);
		try {
			Rectangle area = new Rectangle (0, 0, image.Width, image.Height);
			BitmapData data = image.LockBits (area, ImageLockMode.ReadOnly, PixelFormat.Format32bppArgb);
			byte [] buffer = new byte [Math.Abs (data.Stride) * image.Height];
			Marshal.Copy (data.Scan0, buffer, 0, buffer.Length);
			image.UnlockBits (data);
			List<int> runs = new List<int> ();
			for (int fixed1 = 0; fixed1 < (axis == 0 ? image.Height : image.Width); fixed1 ++) {
				int run = 0;
				int span = axis == 0 ? image.Width : image.Height;
				for (int along = 0; along < span; along ++) {
					int offset = axis == 0 ? fixed1 * data.Stride + along * 4 : along * data.Stride + fixed1 * 4;
					int dr = Math.Abs (buffer [offset + 2] - red), dg = Math.Abs (buffer [offset + 1] - green),
							db = Math.Abs (buffer [offset] - blue);
					if (dr <= tolerance && dg <= tolerance && db <= tolerance) {
						run ++;
					} else if (run > 0) {
						if (run <= 40) runs.Add (run);
						run = 0;
					}
				}
				if (run > 0 && run <= 40) runs.Add (run);
			}
			runs.Sort ();
			result ["count"] = runs.Count;
			result ["median"] = runs.Count == 0 ? 0 : runs [runs.Count / 2];
			result ["p90"] = runs.Count == 0 ? 0 : runs [Math.Min (runs.Count - 1, runs.Count * 90 / 100)];
			result ["maximum"] = runs.Count == 0 ? 0 : runs [runs.Count - 1];
		} finally { image.Dispose (); }
		return result;
	}

	/*
		Separate strokes of one colour, grouped perpendicular to their length: axis 0 counts the
		vertical lines (a column belongs to a line when it holds a vertical run of minimumRun
		pixels), axis 1 the horizontal lines. The group size is the drawn width of the core, so
		this measures how many marks are on the screen and how thick each of them really is.
	*/
	public static Hashtable Strokes (string path, int red, int green, int blue, int tolerance, int axis, int minimumRun) {
		Hashtable result = new Hashtable ();
		if (! File.Exists (path)) { result ["error"] = "missing"; return result; }
		Bitmap image = Load (path);
		try {
			Rectangle area = new Rectangle (0, 0, image.Width, image.Height);
			BitmapData data = image.LockBits (area, ImageLockMode.ReadOnly, PixelFormat.Format32bppArgb);
			byte [] buffer = new byte [Math.Abs (data.Stride) * image.Height];
			Marshal.Copy (data.Scan0, buffer, 0, buffer.Length);
			image.UnlockBits (data);
			int across = axis == 0 ? image.Width : image.Height;
			int along = axis == 0 ? image.Height : image.Width;
			List<int> widths = new List<int> ();
			List<int> lengths = new List<int> ();
			int group = 0, groupLongest = 0;
			for (int line = 0; line < across; line ++) {
				int longest = 0, run = 0;
				for (int step = 0; step < along; step ++) {
					int offset = axis == 0 ? step * data.Stride + line * 4 : line * data.Stride + step * 4;
					int dr = Math.Abs (buffer [offset + 2] - red), dg = Math.Abs (buffer [offset + 1] - green),
							db = Math.Abs (buffer [offset] - blue);
					if (dr <= tolerance && dg <= tolerance && db <= tolerance) {
						run ++;
						if (run > longest) longest = run;
					} else run = 0;
				}
				bool belongs = longest >= minimumRun;
				if (belongs) {
					group ++;
					if (longest > groupLongest) groupLongest = longest;
				} else if (group > 0) {
					widths.Add (group); lengths.Add (groupLongest); group = 0; groupLongest = 0;
				}
			}
			if (group > 0) { widths.Add (group); lengths.Add (groupLongest); }
			widths.Sort ();
			result ["strokes"] = widths.Count;
			result ["medianWidth"] = widths.Count == 0 ? 0 : widths [widths.Count / 2];
			result ["maximumWidth"] = widths.Count == 0 ? 0 : widths [widths.Count - 1];
			result ["longestMedian"] = lengths.Count == 0 ? 0 : lengths [lengths.Count / 2];
			result ["pixels"] = 0;
			for (int width1 = 0; width1 < widths.Count; width1 ++) result ["pixels"] = (int) result ["pixels"] + widths [width1] * lengths [width1];
		} finally { image.Dispose (); }
		return result;
	}
	public static double BandFraction (string path, int top, int bottom, int red, int green, int blue, int tolerance) {
		if (! File.Exists (path)) return -1;
		Bitmap image = Load (path);
		try {
			Rectangle area = new Rectangle (0, 0, image.Width, image.Height);
			BitmapData data = image.LockBits (area, ImageLockMode.ReadOnly, PixelFormat.Format32bppArgb);
			byte [] buffer = new byte [Math.Abs (data.Stride) * image.Height];
			Marshal.Copy (data.Scan0, buffer, 0, buffer.Length);
			image.UnlockBits (data);
			int lower = Math.Max (0, Math.Min (top, bottom)), upper = Math.Min (image.Height, Math.Max (top, bottom));
			if (upper <= lower) return -1;
			long hit = 0, total = 0;
			for (int y = lower; y < upper; y ++)
				for (int x = 0; x < image.Width; x ++) {
					int offset = y * data.Stride + x * 4;
					total ++;
					if (Math.Abs (buffer [offset + 2] - red) <= tolerance && Math.Abs (buffer [offset + 1] - green) <= tolerance
							&& Math.Abs (buffer [offset] - blue) <= tolerance) hit ++;
			}
			return (double) hit / total;
		} finally { image.Dispose (); }
	}

	/* Mean colour of the pixels inside a rectangle, used to show through-transparency mixing. */
	public static int [] MeanColour (string path, int left, int top, int right, int bottom) {
		if (! File.Exists (path)) return new int [] { -1, -1, -1 };
		Bitmap image = Load (path);
		try {
			Rectangle area = new Rectangle (0, 0, image.Width, image.Height);
			BitmapData data = image.LockBits (area, ImageLockMode.ReadOnly, PixelFormat.Format32bppArgb);
			byte [] buffer = new byte [Math.Abs (data.Stride) * image.Height];
			Marshal.Copy (data.Scan0, buffer, 0, buffer.Length);
			image.UnlockBits (data);
			long red = 0, green = 0, blue = 0, count = 0;
			for (int y = Math.Max (0, top); y < Math.Min (image.Height, bottom); y ++)
				for (int x = Math.Max (0, left); x < Math.Min (image.Width, right); x ++) {
					int offset = y * data.Stride + x * 4;
					red += buffer [offset + 2]; green += buffer [offset + 1]; blue += buffer [offset]; count ++;
				}
			if (count == 0) return new int [] { -1, -1, -1 };
			return new int [] { (int) (red / count), (int) (green / count), (int) (blue / count) };
		} finally { image.Dispose (); }
	}

	/*
		Comparison of two screen captures of the same rectangle, taken while the window was
		opaque and while it was translucent over a backdrop of one known colour. The second
		count is the proof that the compositor really mixes the desktop into the window.
	*/
	public static Hashtable PairBleed (string opaquePath, string translucentPath, int red, int green, int blue, int percent, int tolerance) {
		Hashtable result = new Hashtable ();
		if (! File.Exists (opaquePath) || ! File.Exists (translucentPath)) { result ["error"] = "missing"; return result; }
		Bitmap first = Load (opaquePath), second = Load (translucentPath);
		try {
			if (first.Width != second.Width || first.Height != second.Height) { result ["error"] = "size differs"; return result; }
			Rectangle area = new Rectangle (0, 0, first.Width, first.Height);
			BitmapData a = first.LockBits (area, ImageLockMode.ReadOnly, PixelFormat.Format32bppArgb);
			BitmapData b = second.LockBits (area, ImageLockMode.ReadOnly, PixelFormat.Format32bppArgb);
			byte [] bufferA = new byte [Math.Abs (a.Stride) * first.Height];
			byte [] bufferB = new byte [Math.Abs (b.Stride) * first.Height];
			Marshal.Copy (a.Scan0, bufferA, 0, bufferA.Length);
			Marshal.Copy (b.Scan0, bufferB, 0, bufferB.Length);
			first.UnlockBits (a); second.UnlockBits (b);
			double keep = (double) percent / 100.0;
			long white = 0, bleed = 0, changed = 0;
			for (int y = 0; y < first.Height; y ++)
				for (int x = 0; x < first.Width; x ++) {
					int offset = y * a.Stride + x * 4;
					byte blueA = bufferA [offset], greenA = bufferA [offset + 1], redA = bufferA [offset + 2];
					int offsetB = y * b.Stride + x * 4;
					byte blueB = bufferB [offsetB], greenB = bufferB [offsetB + 1], redB = bufferB [offsetB + 2];
					if (redA != 255 || greenA != 255 || blueA != 255) continue;
					white ++;
					if (Math.Abs (redA - redB) > 1 || Math.Abs (greenA - greenB) > 1 || Math.Abs (blueA - blueB) > 1) changed ++;
					int expectedRed = (int) Math.Round (keep * 255 + (1 - keep) * red);
					int expectedGreen = (int) Math.Round (keep * 255 + (1 - keep) * green);
					int expectedBlue = (int) Math.Round (keep * 255 + (1 - keep) * blue);
					if (Math.Abs (redB - expectedRed) <= tolerance && Math.Abs (greenB - expectedGreen) <= tolerance
							&& Math.Abs (blueB - expectedBlue) <= tolerance) bleed ++;
				}
			result ["white"] = white; result ["bleed"] = bleed; result ["changed"] = changed;
			result ["pixels"] = (long) first.Width * first.Height;
		} finally { first.Dispose (); second.Dispose (); }
		return result;
	}

	/* How much two captures of the same area differ: mean per-channel distance and change count. */
	public static Hashtable Diff (string firstPath, string secondPath) {
		Hashtable result = new Hashtable ();
		if (! File.Exists (firstPath) || ! File.Exists (secondPath)) { result ["error"] = "missing"; return result; }
		Bitmap first = Load (firstPath), second = Load (secondPath);
		try {
			if (first.Width != second.Width || first.Height != second.Height) { result ["error"] = "size differs"; return result; }
			Rectangle area = new Rectangle (0, 0, first.Width, first.Height);
			BitmapData a = first.LockBits (area, ImageLockMode.ReadOnly, PixelFormat.Format32bppArgb);
			BitmapData b = second.LockBits (area, ImageLockMode.ReadOnly, PixelFormat.Format32bppArgb);
			byte [] bufferA = new byte [Math.Abs (a.Stride) * first.Height];
			byte [] bufferB = new byte [Math.Abs (b.Stride) * first.Height];
			Marshal.Copy (a.Scan0, bufferA, 0, bufferA.Length);
			Marshal.Copy (b.Scan0, bufferB, 0, bufferB.Length);
			first.UnlockBits (a); second.UnlockBits (b);
			long difference = 0, changed = 0;
			for (int y = 0; y < first.Height; y ++)
				for (int x = 0; x < first.Width; x ++) {
					int offset = y * a.Stride + x * 4, offsetB = y * b.Stride + x * 4;
					int local = Math.Abs (bufferA [offset] - bufferB [offsetB]) + Math.Abs (bufferA [offset + 1] - bufferB [offsetB + 1])
							+ Math.Abs (bufferA [offset + 2] - bufferB [offsetB + 2]);
					difference += local;
					if (local > 12) changed ++;
				}
			long pixels = (long) first.Width * first.Height;
			result ["meanDifference"] = (double) difference / (pixels * 3);
			result ["changed"] = changed; result ["pixels"] = pixels;
		} finally { first.Dispose (); second.Dispose (); }
		return result;
	}

	/*
		Per-row difference between two captures, so a check can tell which horizontal bands of a
		window changed and which kept their original look.
	*/
	public static int [] RowChanges (string firstPath, string secondPath, int step) {
		return RowChanges (firstPath, secondPath, step, 0, int.MaxValue);
	}
	public static int [] RowChanges (string firstPath, string secondPath, int step, int left, int right) {
		if (! File.Exists (firstPath) || ! File.Exists (secondPath)) return new int [] { -1 };
		Bitmap first = Load (firstPath), second = Load (secondPath);
		try {
			if (first.Width != second.Width || first.Height != second.Height) return new int [] { -2 };
			Rectangle area = new Rectangle (0, 0, first.Width, first.Height);
			BitmapData a = first.LockBits (area, ImageLockMode.ReadOnly, PixelFormat.Format32bppArgb);
			BitmapData b = second.LockBits (area, ImageLockMode.ReadOnly, PixelFormat.Format32bppArgb);
			byte [] bufferA = new byte [Math.Abs (a.Stride) * first.Height];
			byte [] bufferB = new byte [Math.Abs (b.Stride) * first.Height];
			Marshal.Copy (a.Scan0, bufferA, 0, bufferA.Length);
			Marshal.Copy (b.Scan0, bufferB, 0, bufferB.Length);
			first.UnlockBits (a); second.UnlockBits (b);
			int rows = (first.Height + step - 1) / step;
			int [] changes = new int [rows];
			for (int row = 0; row < rows; row ++) {
				int y = row * step;
				long changed = 0;
				for (int x = Math.Max (0, left); x < Math.Min (first.Width, right); x ++) {
					int offset = y * a.Stride + x * 4, offsetB = y * b.Stride + x * 4;
					if (Math.Abs (bufferA [offset] - bufferB [offsetB]) + Math.Abs (bufferA [offset + 1] - bufferB [offsetB + 1])
							+ Math.Abs (bufferA [offset + 2] - bufferB [offsetB + 2]) > 12) changed ++;
				}
				changes [row] = (int) changed;
			}
			return changes;
		} finally { first.Dispose (); second.Dispose (); }
	}

	public static string Sha256OfFile (string path) {
		using (SHA256 hash = SHA256.Create ())
		using (Stream stream = File.OpenRead (path))
			return BitConverter.ToString (hash.ComputeHash (stream)).Replace ("-", "");
	}

	/*
		All the pixels inside the tolerance box around one colour, averaged. The harness feeds the
		result to ContrastRatio, so the reported contrast comes from the pixels on the screen rather
		than from the constants in the source.
	 */
	public static Hashtable MeanOfColour (string path, int red, int green, int blue, int tolerance) {
		Hashtable result = new Hashtable ();
		result ["count"] = 0L; result ["red"] = 0; result ["green"] = 0; result ["blue"] = 0;
		if (! File.Exists (path)) { result ["error"] = "missing"; return result; }
		Bitmap image = Load (path);
		try {
			Rectangle area = new Rectangle (0, 0, image.Width, image.Height);
			BitmapData data = image.LockBits (area, ImageLockMode.ReadOnly, PixelFormat.Format32bppArgb);
			byte [] buffer = new byte [Math.Abs (data.Stride) * image.Height];
			Marshal.Copy (data.Scan0, buffer, 0, buffer.Length);
			image.UnlockBits (data);
			long count = 0, sumRed = 0, sumGreen = 0, sumBlue = 0;
			for (int pixel = 0; pixel < image.Width * image.Height; pixel ++) {
				int offset = (pixel / image.Width) * data.Stride + (pixel % image.Width) * 4;
				byte actualBlue = buffer [offset], actualGreen = buffer [offset + 1], actualRed = buffer [offset + 2];
				if (Math.Abs (actualRed - red) > tolerance || Math.Abs (actualGreen - green) > tolerance || Math.Abs (actualBlue - blue) > tolerance) continue;
				count ++; sumRed += actualRed; sumGreen += actualGreen; sumBlue += actualBlue;
			}
			result ["count"] = count;
			if (count > 0) {
				result ["red"] = (int) (sumRed / count); result ["green"] = (int) (sumGreen / count); result ["blue"] = (int) (sumBlue / count);
			}
		} finally { image.Dispose (); }
		return result;
	}

	/* The WCAG ratio is the lighter luminance over the darker one, so the argument
		order does not matter and a dark colour on white reports above 1. */
	public static double ContrastRatio (int red1, int green1, int blue1, int red2, int green2, int blue2) {
		double first = Luminance (red1, green1, blue1);
		double second = Luminance (red2, green2, blue2);
		double lighter = Math.Max (first, second);
		double darker = Math.Min (first, second);
		return (lighter + 0.05) / (darker + 0.05);
	}
	private static double Luminance (int red, int green, int blue) {
		return 0.2126 * Channel (red) + 0.7152 * Channel (green) + 0.0722 * Channel (blue);
	}
	private static double Channel (int value) {
		double linear = value / 255.0;
		return linear <= 0.03928 ? linear / 12.92 : Math.Pow ((linear + 0.055) / 1.055, 2.4);
	}

	public static uint [] ClipboardFormats () {
		List<uint> formats = new List<uint> ();
		if (! OpenClipboard (IntPtr.Zero)) return formats.ToArray ();
		try {
			uint format = 0;
			do { format = EnumClipboardFormats (format); if (format != 0) formats.Add (format); }
			while (format != 0 && formats.Count < 200);
		} finally { CloseClipboard (); }
		return formats.ToArray ();
	}

	public static string ClipboardFormatName (uint format) {
		StringBuilder buffer = new StringBuilder (256);
		GetClipboardFormatName (format, buffer, buffer.Capacity);
		string name = buffer.ToString ();
		if (name.Length > 0) return name;
		switch (format) {
			case 13: return "CF_UNICODETEXT";
			case 8: return "CF_DIB";
			case 17: return "CF_DIBV5";
			case 1: return "CF_TEXT";
			case 2: return "CF_BITMAP";
			default: return "format-" + format;
		}
	}
	[DllImport("user32.dll", CharSet=CharSet.Unicode)] private static extern int GetClipboardFormatName (uint format, StringBuilder buffer, int maximum);

	public static string ClipboardText () {
		if (! OpenClipboard (IntPtr.Zero)) return null;
		try {
			IntPtr memory = GetClipboardData (CF_UNICODETEXT);
			if (memory == IntPtr.Zero) return null;
			IntPtr address = GlobalLock (memory);
			string text = address == IntPtr.Zero ? null : Marshal.PtrToStringUni (address);
			if (address != IntPtr.Zero) GlobalUnlock (memory);
			return text;
		} finally { CloseClipboard (); }
	}

	public static bool ClearClipboard () {
		if (! OpenClipboard (IntPtr.Zero)) return false;
		try { return EmptyClipboard (); } finally { CloseClipboard (); }
	}

	/*
		The CF_DIB block on the clipboard, analysed as bytes: dimensions, bit count, the alpha
		channel of every pixel and the counts of the colours we care about. No file is written.
	*/
	public static Hashtable ClipboardDib (int [] colours, int tolerance) {
		Hashtable result = new Hashtable ();
		if (! OpenClipboard (IntPtr.Zero)) { result ["error"] = "clipboard busy"; return result; }
		try {
			IntPtr memory = GetClipboardData (CF_DIB);
			if (memory == IntPtr.Zero) { result ["error"] = "no CF_DIB"; return result; }
			long size = GlobalSize (memory).ToInt64 ();
			IntPtr address = GlobalLock (memory);
			if (address == IntPtr.Zero) { result ["error"] = "locked"; return result; }
			byte [] buffer;
			try { buffer = new byte [size]; Marshal.Copy (address, buffer, 0, (int) size); }
			finally { GlobalUnlock (memory); }
			int headerSize = BitConverter.ToInt32 (buffer, 0);
			int width = BitConverter.ToInt32 (buffer, 4);
			int height = BitConverter.ToInt32 (buffer, 8);
			short bitCount = BitConverter.ToInt16 (buffer, 14);
			int compression = BitConverter.ToInt32 (buffer, 16);
			result ["headerSize"] = headerSize; result ["width"] = width; result ["height"] = height;
			result ["bitCount"] = (int) bitCount; result ["compression"] = compression;
			result ["bytes"] = size;
			int [] hashSource = new int [] { width, height, bitCount };
			if (bitCount != 32) { result ["error"] = "not 32 bpp"; return result; }
			int stride = ((width * 32 + 31) / 32) * 4;
			int pixelOffset = headerSize;
			if (pixelOffset + (long) stride * Math.Abs (height) > size)
				pixelOffset = 14 + buffer [12];   // after the DIBV5 colour tables when present
			result ["pixelOffset"] = pixelOffset;
			if (pixelOffset + (long) stride * Math.Abs (height) > size) { result ["error"] = "truncated"; return result; }
			int [] counts = new int [colours.Length / 3];
			long nonOpaque = 0, white = 0; int minAlpha = 255, maxAlpha = 0;
			for (int y = 0; y < Math.Abs (height); y ++)
				for (int x = 0; x < width; x ++) {
					int offset = pixelOffset + y * stride + x * 4;
					byte blue = buffer [offset], green = buffer [offset + 1], red = buffer [offset + 2], alpha = buffer [offset + 3];
					if (alpha < minAlpha) minAlpha = alpha;
					if (alpha > maxAlpha) maxAlpha = alpha;
					if (alpha != 255) nonOpaque ++;
					if (red == 255 && green == 255 && blue == 255) white ++;
					for (int colour = 0; colour < counts.Length; colour ++) {
						int dr = Math.Abs (red - colours [colour * 3]), dg = Math.Abs (green - colours [colour * 3 + 1]),
								db = Math.Abs (blue - colours [colour * 3 + 2]);
						if (dr <= tolerance && dg <= tolerance && db <= tolerance) counts [colour] ++;
					}
				}
			result ["counts"] = counts; result ["white"] = white;
			result ["minAlpha"] = minAlpha; result ["maxAlpha"] = maxAlpha; result ["nonOpaque"] = nonOpaque;
			result ["sha256"] = BitConverter.ToString (SHA256.Create ().ComputeHash (buffer, pixelOffset,
					(int) Math.Min (size - pixelOffset, (long) stride * Math.Abs (height)))).Replace ("-", "");
		} finally { CloseClipboard (); }
		return result;
	}

	[DllImport("user32.dll", CharSet=CharSet.Unicode)] private static extern uint RegisterClipboardFormatW (string format);

	/* The registered "PNG" format, the one the editor writes for image consumers. */
	public static uint PngFormat () { return RegisterClipboardFormatW ("PNG"); }

	/*
		The raw block the clipboard holds for one format, as it would be handed to a program
		that pastes it. Used to write the editor's own image to a file for measurement.
	*/
	public static byte [] ClipboardBytes (uint format) {
		if (! OpenClipboard (IntPtr.Zero)) return null;
		try {
			IntPtr memory = GetClipboardData (format);
			if (memory == IntPtr.Zero) return null;
			long size = GlobalSize (memory).ToInt64 ();
			IntPtr address = GlobalLock (memory);
			if (address == IntPtr.Zero) return null;
			byte [] buffer = new byte [size];
			Marshal.Copy (address, buffer, 0, (int) size);
			GlobalUnlock (memory);
			return buffer;
		} finally { CloseClipboard (); }
	}

	/*
		The image on the clipboard as a PNG file: the encoded PNG when the clipboard has one,
		otherwise the CF_DIB or CF_DIBV5 block with a bitmap file header in front of it.
	*/
	public static bool ClipboardSavePng (string path) {
		byte [] png = ClipboardBytes (PngFormat ());
		if (png != null && png.Length > 8) {
			File.WriteAllBytes (path, png);
			return File.Exists (path);
		}
		byte [] dib = ClipboardBytes (CF_DIBV5);
		if (dib == null) dib = ClipboardBytes (CF_DIB);
		if (dib == null || dib.Length < 40) return false;
		int headerSize = BitConverter.ToInt32 (dib, 0);
		int bitCount = BitConverter.ToInt16 (dib, 14);
		int coloursUsed = BitConverter.ToInt32 (dib, 32);
		if (coloursUsed == 0 && bitCount <= 8) coloursUsed = 1 << bitCount;
		int offBits = 14 + headerSize + coloursUsed * 4;
		byte [] bitmapFile = new byte [14 + dib.Length];
		bitmapFile [0] = (byte) 'B'; bitmapFile [1] = (byte) 'M';
		int fileSize = 14 + dib.Length;
		Array.Copy (BitConverter.GetBytes (fileSize), 0, bitmapFile, 2, 4);
		Array.Copy (BitConverter.GetBytes (offBits), 0, bitmapFile, 10, 4);
		Array.Copy (dib, 0, bitmapFile, 14, dib.Length);
		using (MemoryStream source = new MemoryStream (bitmapFile))
			using (Bitmap image = new Bitmap (source))
				image.Save (path, ImageFormat.Png);
		return File.Exists (path);
	}
}
