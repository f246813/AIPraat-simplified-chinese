#include "../../sys/praat_python_wait.h"
#include <cassert>
#include <cstdio>
#include <thread>

static int paints = 0, commands = 0;
static LRESULT CALLBACK procedure (HWND window, UINT message, WPARAM first, LPARAM second) {
	if (message == WM_COMMAND) {
		commands ++;
		return 0;
	}
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
	ShowWindow (window, SW_SHOWNOACTIVATE);
	ShowWindow (disabled, SW_SHOWNOACTIVATE);
	HANDLE completion = CreateEventW (nullptr, TRUE, FALSE, nullptr);
	std::thread worker ([&] { Sleep (100); SetEvent (completion); });
	{
		praat_python_WindowsWait wait;
		assert (! IsWindowEnabled (window));
		InvalidateRect (window, nullptr, FALSE);
		PostMessageW (window, WM_COMMAND, 1, 0);
		assert (wait.wait (completion, 2000) == WAIT_OBJECT_0);
		assert (paints > 0);
		assert (commands == 0); // No nested Python/menu actions during the snapshot.
		ResetEvent (completion);
		assert (wait.wait (completion, 20) == WAIT_TIMEOUT);
	}
	worker.join ();
	assert (IsWindowEnabled (window));
	assert (! IsWindowEnabled (disabled));
	MSG message;
	while (PeekMessageW (& message, nullptr, WM_COMMAND, WM_COMMAND, PM_REMOVE))
		DispatchMessageW (& message);
	assert (commands == 1); // The normal event loop can now handle deferred work.
	try {
		praat_python_WindowsWait wait;
		throw 1;
	} catch (int) { }
	assert (IsWindowEnabled (window));
	CloseHandle (completion);
	DestroyWindow (window);
	DestroyWindow (disabled);
	std::puts ("PASS repaint, completion, timeout, deferred commands, disabled-state and exception restoration");
}
