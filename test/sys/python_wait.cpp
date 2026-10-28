#include "../../sys/praat_python_wait.h"
#include <cassert>
#include <cstdio>
#include <thread>

static int paints = 0, commands = 0, ipc = 0, timers = 0, moveLoops = 0;
static void CALLBACK timerCallback (HWND, UINT, UINT_PTR, DWORD) { timers ++; }
static LRESULT CALLBACK procedure (HWND window, UINT message, WPARAM first, LPARAM second) {
	if (message == WM_COMMAND) {
		commands ++;
		return 0;
	}
	if (message == WM_APP) { ipc ++; return 1; }
	if (message == WM_ENTERSIZEMOVE) moveLoops ++;
	if (message == WM_PAINT) {
		PAINTSTRUCT paint;
		BeginPaint (window, & paint);
		EndPaint (window, & paint);
		paints ++;
		return 0;
	}
	return DefWindowProcW (window, message, first, second);
}

int main () {
	WNDCLASSW type { };
	type.lpfnWndProc = procedure;
	type.hInstance = GetModuleHandleW (nullptr);
	type.lpszClassName = L"PythonWaitTest";
	RegisterClassW (& type);
	HWND window = CreateWindowW (type.lpszClassName, L"Python wait fixture", WS_OVERLAPPEDWINDOW,
		0, 0, 120, 80, nullptr, nullptr, type.hInstance, nullptr);
	HWND disabled = CreateWindowW (type.lpszClassName, L"Disabled fixture", WS_OVERLAPPEDWINDOW | WS_DISABLED,
		0, 0, 120, 80, nullptr, nullptr, type.hInstance, nullptr);
	HWND control = CreateWindowW (L"BUTTON", L"Action", WS_CHILD | WS_VISIBLE,
		5, 5, 40, 20, window, nullptr, type.hInstance, nullptr);
	HWND disabledControl = CreateWindowW (L"BUTTON", L"Disabled", WS_CHILD | WS_DISABLED,
		5, 25, 40, 20, window, nullptr, type.hInstance, nullptr);
	ShowWindow (window, SW_SHOWNOACTIVATE);
	ShowWindow (disabled, SW_SHOWNOACTIVATE);
	HANDLE completion = CreateEventW (nullptr, TRUE, FALSE, nullptr);
	std::thread worker ([&] { Sleep (100); SetEvent (completion); });
	{
		praat_python_WindowsWait wait;
		assert (IsWindowEnabled (window));
		assert (! IsWindowEnabled (control));
		assert (! IsWindowEnabled (disabledControl));
		InvalidateRect (window, nullptr, FALSE);
		PostMessageW (window, WM_COMMAND, 1, 0);
		SendMessageW (window, WM_COMMAND, 2, 0);
		SendMessageW (window, WM_APP, 0, 0);
		assert (wait.wait (completion, 2000) == WAIT_OBJECT_0);
		assert (paints > 0);
		assert (commands == 0); // No nested Python/menu actions during the snapshot.
		assert (ipc == 0);
		ResetEvent (completion);
		assert (wait.wait (completion, 20) == WAIT_TIMEOUT);
	}
	worker.join ();
	assert (IsWindowEnabled (window));
	assert (IsWindowEnabled (control));
	assert (! IsWindowEnabled (disabledControl));
	assert (! IsWindowEnabled (disabled));
	MSG message;
	while (PeekMessageW (& message, nullptr, WM_COMMAND, WM_COMMAND, PM_REMOVE))
		DispatchMessageW (& message);
	assert (commands == 1); // The normal event loop can now handle deferred work.

	RECT before, after;
	SetForegroundWindow (window);
	SetActiveWindow (window);
	GetWindowRect (window, & before);
	ResetEvent (completion);
	std::thread mover ([&] {
		Sleep (100);
		PostMessageW (window, WM_KEYDOWN, VK_RIGHT, 1);
		Sleep (50);
		PostMessageW (window, WM_KEYDOWN, VK_RIGHT, 1);
		Sleep (50);
		PostMessageW (window, WM_KEYDOWN, VK_RIGHT, 1);
		Sleep (50);
		PostMessageW (window, WM_KEYDOWN, VK_RETURN, 1);
		Sleep (100);
		PostMessageW (window, WM_CANCELMODE, 0, 0);
		SetEvent (completion);
	});
	{
		praat_python_WindowsWait wait;
		SetTimer (window, 1, 10, timerCallback);
		PostMessageW (window, WM_COMMAND, 3, 0);
		PostMessageW (window, WM_APP, 0, 0);
		PostMessageW (window, WM_SYSCOMMAND, SC_MOVE, 0);
		assert (wait.wait (completion, 2000) == WAIT_OBJECT_0);
		assert (moveLoops > 0);
		assert (commands == 1 && ipc == 0 && timers == 0);
		GetWindowRect (window, & after);
		assert (after.left > before.left);
	}
	mover.join ();
	while (PeekMessageW (& message, nullptr, 0, 0, PM_REMOVE))
		DispatchMessageW (& message);
	KillTimer (window, 1);
	assert (commands == 2 && ipc == 1 && timers > 0);
	{
		praat_python_WindowsWait wait;
		PostMessageW (window, WM_SYSCOMMAND, SC_MAXIMIZE, 0);
		wait.pump ();
		assert (IsZoomed (window));
		PostMessageW (window, WM_SYSCOMMAND, SC_RESTORE, 0);
		wait.pump ();
		assert (! IsZoomed (window));
		SendMessageW (window, WM_SYSCOMMAND, SC_CLOSE, 0);
		assert (IsWindow (window));
	}
	try {
		praat_python_WindowsWait wait;
		throw 1;
	} catch (int) { }
	assert (IsWindowEnabled (window));
	CloseHandle (completion);
	DestroyWindow (window);
	DestroyWindow (disabled);
	std::puts ("PASS native frame move/restore, repaint, completion, timeout, deferred menu/IPC/timer callbacks, controls and exception restoration");
}
