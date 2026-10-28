/* Exercise the Objects window's native list and real Praat selection callback. */
#include "../../sys/GuiP.h"
#include "../../sys/praat.h"
#include "../../fon/Sound.h"
#include <fstream>
#include <iostream>
#include <vector>

static HWND objectList;
static int menus = 0;

static BOOL CALLBACK findList (HWND hwnd, LPARAM) {
	wchar_t name [64] {};
	GetClassNameW (hwnd, name, 64);
	if (wcscmp (name, L"ListBox") == 0)
		objectList = hwnd;
	return TRUE;
}

static BOOL CALLBACK findWindow (HWND hwnd, LPARAM) {
	EnumChildWindows (hwnd, findList, 0);
	return TRUE;
}

static void contextMenu (Thing, GuiList_ContextMenuEvent) { menus ++; }

static void pump (DWORD milliseconds) {
	const ULONGLONG end = GetTickCount64 () + milliseconds;
	do {
		MSG message;
		while (PeekMessageW (& message, nullptr, 0, 0, PM_REMOVE)) {
			// Mouse scenarios use synchronous messages, independent of the physical pointer.
			if (message.message >= WM_MOUSEFIRST && message.message <= WM_MOUSELAST)
				continue;
			TranslateMessage (& message);
			DispatchMessageW (& message);
		}
		Sleep (1);
	} while (GetTickCount64 () < end);
}

static void expect (std::initializer_list <int> indices) {
	const int count = (int) SendMessageW (objectList, LB_GETCOUNT, 0, 0);
	std::vector <int> selected;
	for (int i = 0; i < count; i ++)
		if (SendMessageW (objectList, LB_GETSEL, i, 0) > 0)
			selected.push_back (i);
	if (selected != std::vector <int> (indices)) {
		std::cerr << "Selected:";
		for (int i : selected) std::cerr << " " << i;
		std::cerr << "; expected:";
		for (int i : indices) std::cerr << " " << i;
		std::cerr << "\n";
		Melder_throw (U"Native selection mismatch.");
	}
	Melder_assert (theCurrentPraatObjects -> totalSelection == (integer) selected.size());
	for (int i = 0; i < count; i ++)
		Melder_assert (theCurrentPraatObjects -> list [i + 1]. isSelected ==
			(SendMessageW (objectList, LB_GETSEL, i, 0) > 0));
}

static void select (std::initializer_list <int> indices) {
	SendMessageW (objectList, LB_SETSEL, FALSE, -1);
	for (int index : indices)
		SendMessageW (objectList, LB_SETSEL, TRUE, index);
	SendMessageW (GetParent (objectList), WM_COMMAND,
		MAKEWPARAM (GetDlgCtrlID (objectList), LBN_SELCHANGE), (LPARAM) objectList);
	expect (indices);
}

static void mouse (UINT message, int x, int y, WPARAM modifiers = 0) {
	SendMessageW (objectList, message, modifiers, MAKELPARAM (x, y));
}

static void screenshot (const char *path) {
	HWND root = GetAncestor (objectList, GA_ROOT);
	RECT rect {};
	GetClientRect (root, & rect);
	HDC dc = GetDC (root), memory = CreateCompatibleDC (dc);
	HBITMAP bitmap = CreateCompatibleBitmap (dc, rect.right, rect.bottom);
	HGDIOBJ previous = SelectObject (memory, bitmap);
	BitBlt (memory, 0, 0, rect.right, rect.bottom, dc, 0, 0, SRCCOPY);
	SelectObject (memory, previous);
	BITMAPINFO info {};
	info.bmiHeader.biSize = sizeof (BITMAPINFOHEADER);
	info.bmiHeader.biWidth = rect.right;
	info.bmiHeader.biHeight = -rect.bottom;
	info.bmiHeader.biPlanes = 1;
	info.bmiHeader.biBitCount = 32;
	std::vector <unsigned char> pixels (rect.right * rect.bottom * 4);
	Melder_assert (GetDIBits (dc, bitmap, 0, rect.bottom, pixels.data (), & info, DIB_RGB_COLORS));
	BITMAPFILEHEADER header {};
	header.bfType = 0x4d42;
	header.bfOffBits = sizeof (header) + sizeof (BITMAPINFOHEADER);
	header.bfSize = header.bfOffBits + (DWORD) pixels.size ();
	std::ofstream file (path, std::ios::binary);
	file.write ((const char *) & header, sizeof (header));
	file.write ((const char *) & info.bmiHeader, sizeof (BITMAPINFOHEADER));
	file.write ((const char *) pixels.data (), pixels.size ());
	Melder_assert (file.good ());
	DeleteObject (bitmap);
	DeleteDC (memory);
	ReleaseDC (root, dc);
}

static std::vector <DWORD> blankPixels (int rowHeight) {
	RECT client {};
	GetClientRect (objectList, & client);
	const int top = 2 * rowHeight + 8, width = client.right - 16, height = client.bottom - top - 8;
	HDC dc = GetDC (objectList), memory = CreateCompatibleDC (dc);
	HBITMAP bitmap = CreateCompatibleBitmap (dc, width, height);
	HGDIOBJ previous = SelectObject (memory, bitmap);
	BitBlt (memory, 0, 0, width, height, dc, 8, top, SRCCOPY);
	SelectObject (memory, previous);
	BITMAPINFO info {};
	info.bmiHeader.biSize = sizeof (BITMAPINFOHEADER);
	info.bmiHeader.biWidth = width;
	info.bmiHeader.biHeight = -height;
	info.bmiHeader.biPlanes = 1;
	info.bmiHeader.biBitCount = 32;
	std::vector <DWORD> pixels (width * height);
	const bool captured = GetDIBits (dc, bitmap, 0, height, pixels.data (), & info, DIB_RGB_COLORS) != 0;
	DeleteObject (bitmap);
	DeleteDC (memory);
	ReleaseDC (objectList, dc);
	Melder_assert (captured);
	for (auto & pixel : pixels)
		pixel &= 0x00ffffff;
	return pixels;
}

static void expectBlankPixels (const std::vector <DWORD> & expected, int rowHeight, conststring32 scenario) {
	const auto actual = blankPixels (rowHeight);
	if (actual != expected) {
		integer different = 0;
		for (size_t i = 0; i < actual.size (); i ++)
			different += actual [i] != expected [i];
		Melder_throw (scenario, U": ", different, U" blank-area pixels differ.");
	}
}

int main (int argc, char **argv) {
	try {
	praat_init (U"ListSelectionTest", U"1.0", 1000, 2026, 10, 20, U"test", U"example.invalid", argc, argv);
	INCLUDE_LIBRARY (praat_uvafon_init)
	EnumThreadWindows (GetCurrentThreadId (), findWindow, 0);
	Melder_assert (objectList);
	auto widget = (GuiObject) GetWindowLongPtr (objectList, GWLP_USERDATA);
	auto list = (GuiList) widget -> userData;
	GuiList_setContextMenuCallback (list, contextMenu, nullptr);
	for (int i = 0; i < 50; i ++)
		praat_new (Sound_createSimple (1, 0.05, 44100), U"audio_", i + 1);
	praat_updateSelection ();
	HWND root = GetAncestor (objectList, GA_ROOT);
	SetWindowPos (root, HWND_TOP, 80, 80, 950, 650, SWP_SHOWWINDOW);
	SetForegroundWindow (root);
	pump (50);
	SendMessageW (objectList, LB_SETTOPINDEX, 0, 0);
	const int h = (int) SendMessageW (objectList, LB_GETITEMHEIGHT, 0, 0);
	select ({ 9 });
	mouse (WM_RBUTTONDOWN, 210, h + 2, MK_RBUTTON);
	expect ({ 9 });
	mouse (WM_MOUSEMOVE, 40, 4 * h - 2, MK_RBUTTON);
	expect ({ 1, 2, 3 });
	pump (30);
	if (const char *path = std::getenv ("PRAAT_LIST_TEST_SCREENSHOT"))
		screenshot (path);
	mouse (WM_RBUTTONUP, 40, 4 * h - 2);
	expect ({ 1, 2, 3 });
	Melder_assert (menus == 0 && GetCapture () != objectList);
	std::cout << "PASS right drag replaces selection and updates Praat objects; release suppresses menu\n";

	mouse (WM_RBUTTONDOWN, 40, 4 * h - 2, MK_RBUTTON);
	mouse (WM_MOUSEMOVE, 210, h + 2, MK_RBUTTON);
	mouse (WM_RBUTTONUP, 210, h + 2);
	expect ({ 1, 2, 3 });
	std::cout << "PASS reverse drag\n";

	select ({ 9 });
	mouse (WM_RBUTTONDOWN, 40, h + 2, MK_RBUTTON | MK_CONTROL);
	mouse (WM_MOUSEMOVE, 210, 3 * h - 2, MK_RBUTTON | MK_CONTROL);
	expect ({ 9 });
	mouse (WM_RBUTTONUP, 210, 3 * h - 2);
	expect ({ 9 });
	menus = 0;
	std::cout << "PASS Ctrl right drag leaves selection to native list even after Ctrl release\n";
	select ({ 9 });
	mouse (WM_RBUTTONDOWN, 40, h + 2, MK_RBUTTON | MK_SHIFT);
	mouse (WM_MOUSEMOVE, 210, 3 * h - 2, MK_RBUTTON | MK_SHIFT);
	mouse (WM_RBUTTONUP, 210, 3 * h - 2, MK_SHIFT);
	expect ({ 1, 2, 9 });
	std::cout << "PASS Shift additive drag\n";

	mouse (WM_RBUTTONDOWN, 40, h + 2, MK_RBUTTON);
	mouse (WM_MOUSEMOVE, 210, 5 * h - 2, MK_RBUTTON);
	SendMessageW (objectList, WM_KEYDOWN, VK_ESCAPE, 0);
	mouse (WM_RBUTTONUP, 210, 5 * h - 2);
	expect ({ 1, 2, 9 });
	Melder_assert (menus == 0 && GetCapture () != objectList);
	std::cout << "PASS Escape restores selection and suppresses menu\n";

	mouse (WM_RBUTTONDOWN, 40, h + 2, MK_RBUTTON);
	mouse (WM_MOUSEMOVE, 210, 5 * h - 2, MK_RBUTTON);
	ReleaseCapture ();
	mouse (WM_RBUTTONUP, 210, 5 * h - 2);
	expect ({ 1, 2, 9 });
	std::cout << "PASS capture loss restores selection\n";

	mouse (WM_RBUTTONDOWN, 40, 6 * h + 2, MK_RBUTTON);
	mouse (WM_RBUTTONUP, 41, 6 * h + 3);
	expect ({ 6 });
	Melder_assert (menus == 1);
	mouse (WM_RBUTTONDOWN, 40, 6 * h + 2, MK_RBUTTON);
	pump (600);
	mouse (WM_RBUTTONUP, 40, 6 * h + 2);
	expect ({ 6 });
	Melder_assert (menus == 2);
	SendMessageW (objectList, WM_CONTEXTMENU, (WPARAM) objectList, MAKELPARAM (-1, -1));
	Melder_assert (menus == 3);
	std::cout << "PASS click jitter and stationary hold retain context menu; keyboard menu works\n";

	SendMessageW (objectList, WM_KEYDOWN, VK_DOWN, 0);
	SendMessageW (objectList, WM_KEYUP, VK_DOWN, 0);
	expect ({ 7 });
	std::cout << "PASS keyboard selection and real callback\n";
	mouse (WM_LBUTTONDOWN, 40, 8 * h + 2, MK_LBUTTON);
	mouse (WM_LBUTTONUP, 40, 8 * h + 2);
	expect ({ 8 });
	std::cout << "PASS ordinary left click and real callback\n";

	RECT client {};
	GetClientRect (objectList, & client);
	mouse (WM_RBUTTONDOWN, 40, h + 2, MK_RBUTTON);
	mouse (WM_MOUSEMOVE, 210, client.bottom + 30, MK_RBUTTON);
	pump (300);
	Melder_assert (SendMessageW (objectList, LB_GETTOPINDEX, 0, 0) > 0);
	Melder_assert (SendMessageW (objectList, LB_GETSEL, 1, 0) > 0);
	SendMessageW (objectList, WM_KEYDOWN, VK_ESCAPE, 0);
	expect ({ 8 });
	std::cout << "PASS edge auto-scroll preserves content anchor; cancel restores objects\n";

	SendMessageW (objectList, LB_SETTOPINDEX, 48, 0);
	SendMessageW (objectList, LB_GETITEMRECT, 49, (LPARAM) & client);
	const int emptyY = client.bottom + 8;
	mouse (WM_RBUTTONDOWN, 40, emptyY, MK_RBUTTON);
	mouse (WM_MOUSEMOVE, 210, emptyY + 12, MK_RBUTTON);
	mouse (WM_RBUTTONUP, 210, emptyY + 12);
	expect ({});
	std::cout << "PASS blank-area drag clears selection\n";
	while (theCurrentPraatObjects -> n > 2)
		praat_removeObject (theCurrentPraatObjects -> n);
	SendMessageW (objectList, LB_SETTOPINDEX, 0, 0);
	select ({ 0, 1 });
	RedrawWindow (objectList, nullptr, nullptr, RDW_INVALIDATE | RDW_ERASE | RDW_UPDATENOW);
	const auto cleanBlank = blankPixels (h);
	BYTE originalKeyboard [256] {}, ctrlKeyboard [256] {};
	GetKeyboardState (originalKeyboard);
	memcpy (ctrlKeyboard, originalKeyboard, sizeof (ctrlKeyboard));
	ctrlKeyboard [VK_CONTROL] = 0x80;
	SetKeyboardState (ctrlKeyboard);
	select ({ 0 });
	mouse (WM_LBUTTONDOWN, 40, h + 2, MK_LBUTTON | MK_CONTROL);
	mouse (WM_LBUTTONUP, 40, h + 2, MK_CONTROL);
	expect ({ 0, 1 });
	mouse (WM_LBUTTONDOWN, 40, 2, MK_LBUTTON | MK_CONTROL);
	mouse (WM_LBUTTONUP, 40, 2, MK_CONTROL);
	expect ({ 1 });
	SetKeyboardState (originalKeyboard);
	UpdateWindow (objectList);
	expectBlankPixels (cleanBlank, h, U"Native Ctrl left multi-selection must not paint a band");
	std::cout << "PASS native Ctrl left click adds and toggles objects with no blue band\n";
	select ({ 0, 1 });
	mouse (WM_RBUTTONDOWN, 120, h / 2, MK_RBUTTON | MK_CONTROL);
	mouse (WM_MOUSEMOVE, 240, 5 * h + 12, MK_RBUTTON | MK_CONTROL);
	UpdateWindow (objectList);
	expectBlankPixels (cleanBlank, h, U"Ctrl right drag must not paint a band");
	mouse (WM_RBUTTONUP, 240, 5 * h + 12, MK_CONTROL);
	UpdateWindow (objectList);
	expectBlankPixels (cleanBlank, h, U"Ctrl right release must leave the background clean");
	std::cout << "PASS Ctrl right drag paints no blue band\n";

	for (bool keyEvent : { true, false }) {
		select ({ 0, 1 });
		mouse (WM_RBUTTONDOWN, 120, h / 2, MK_RBUTTON);
		mouse (WM_MOUSEMOVE, 240, 5 * h + 12, MK_RBUTTON);
		UpdateWindow (objectList);
		Melder_assert (blankPixels (h) != cleanBlank);
		if (keyEvent)
			SendMessageW (objectList, WM_KEYDOWN, VK_CONTROL, 0);
		else
			mouse (WM_MOUSEMOVE, 240, 5 * h + 12, MK_RBUTTON | MK_CONTROL);
		UpdateWindow (objectList);
		expectBlankPixels (cleanBlank, h, U"Ctrl during drag must remove the band");
		expect ({ 0, 1 });
		const int menusBeforeRelease = menus;
		mouse (WM_RBUTTONUP, 240, 5 * h + 12, MK_CONTROL);
		Melder_assert (menus == menusBeforeRelease);
	}
	std::cout << "PASS Ctrl during drag cancels band and restores selection via key or mouse event\n";

	mouse (WM_RBUTTONDOWN, 120, h / 2, MK_RBUTTON);
	for (int x : { 220, 190, 240 }) {
		mouse (WM_MOUSEMOVE, x, 5 * h + 12, MK_RBUTTON);
		UpdateWindow (objectList);
	}
	mouse (WM_RBUTTONUP, 240, 5 * h + 12);
	UpdateWindow (objectList);
	if (const char *path = std::getenv ("PRAAT_LIST_TEST_CLEANUP_SCREENSHOT"))
		screenshot (path);
	expectBlankPixels (cleanBlank, h, U"Release must restore empty background");
	expect ({ 0, 1 });
	std::cout << "PASS two-object drag release restores every blank-area pixel\n";

	mouse (WM_RBUTTONDOWN, 120, h / 2, MK_RBUTTON);
	mouse (WM_MOUSEMOVE, 240, 5 * h + 12, MK_RBUTTON);
	UpdateWindow (objectList);
	const auto firstPaint = blankPixels (h);
	Melder_assert (firstPaint != cleanBlank);
	for (int i = 0; i < 6; i ++) {
		InvalidateRect (objectList, nullptr, FALSE);
		UpdateWindow (objectList);
	}
	expectBlankPixels (firstPaint, h, U"Repeated painting must not accumulate blue");
	mouse (WM_MOUSEMOVE, 70, h + 5, MK_RBUTTON);
	UpdateWindow (objectList);
	expectBlankPixels (cleanBlank, h, U"Shrinking band must clear previous blank-area coverage");
	mouse (WM_MOUSEMOVE, 240, 5 * h + 12, MK_RBUTTON);
	UpdateWindow (objectList);
	SendMessageW (objectList, WM_KEYDOWN, VK_ESCAPE, 0);
	UpdateWindow (objectList);
	expectBlankPixels (cleanBlank, h, U"Escape must restore empty background");
	expect ({ 0, 1 });
	std::cout << "PASS repaint, shrink and Escape leave no blue residue\n";

	mouse (WM_RBUTTONDOWN, 120, h / 2, MK_RBUTTON);
	mouse (WM_MOUSEMOVE, 240, 5 * h + 12, MK_RBUTTON);
	UpdateWindow (objectList);
	ReleaseCapture ();
	UpdateWindow (objectList);
	expectBlankPixels (cleanBlank, h, U"Capture loss must restore empty background");
	expect ({ 0, 1 });
	std::cout << "PASS capture loss clears blank-area overlay\n";
	while (theCurrentPraatObjects -> n > 0)
		praat_removeObject (theCurrentPraatObjects -> n);
	mouse (WM_RBUTTONDOWN, 40, 10, MK_RBUTTON);
	mouse (WM_MOUSEMOVE, 210, 80, MK_RBUTTON);
	mouse (WM_RBUTTONUP, 210, 80);
	Melder_assert (GuiList_getSelectedPositions (list).size == 0 && GetCapture () != objectList);
	std::cout << "PASS empty native list drag\n";
	std::cout << "All native list selection checks passed\n";
	return 0;
	} catch (MelderError) {
		std::cerr << Melder_peek32to8 (Melder_getError ()) << "\n";
		Melder_clearError ();
		return 1;
	}
}
