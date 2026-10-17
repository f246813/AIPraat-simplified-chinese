#ifndef _praat_python_wait_h_
#define _praat_python_wait_h_

#if defined (_WIN32)
#include <windows.h>
#include <algorithm>
#include <vector>

// Python owns a snapshot of the selected objects and the process workspace.
// Keep the wait modal; dispatching menu/timer/IPC callbacks here can re-enter
// the runner and overwrite that workspace. Repainting still keeps Windows live.
class praat_python_WindowsWait {
public:
	praat_python_WindowsWait () {
		EnumThreadWindows (GetCurrentThreadId (), disableWindow, reinterpret_cast <LPARAM> (this));
	}
	~praat_python_WindowsWait () {
		for (HWND window : disabledWindows)
			if (IsWindow (window))
				EnableWindow (window, TRUE);
	}
	praat_python_WindowsWait (const praat_python_WindowsWait &) = delete;
	praat_python_WindowsWait & operator= (const praat_python_WindowsWait &) = delete;

	void pump () const {
		MSG message;
		// Peek also services sent messages and resets the hung-window watchdog.
		// Leave queued application commands in place for the normal event loop.
		PeekMessageW (& message, nullptr, 0, 0, PM_NOREMOVE);
		while (PeekMessageW (& message, nullptr, WM_PAINT, WM_PAINT, PM_REMOVE))
			DispatchMessageW (& message);
	}

	DWORD wait (HANDLE process, DWORD milliseconds) const {
		const ULONGLONG started = GetTickCount64 ();
		for (;;) {
			pump ();
			const DWORD state = WaitForSingleObject (process, 0);
			if (state != WAIT_TIMEOUT)
				return state;
			const ULONGLONG elapsed = GetTickCount64 () - started;
			if (milliseconds != INFINITE && elapsed >= milliseconds)
				return WAIT_TIMEOUT;
			const DWORD delay = milliseconds == INFINITE ? 10 :
				std::min <DWORD> (10, milliseconds - static_cast <DWORD> (elapsed));
			const DWORD waited = WaitForSingleObject (process, delay);
			if (waited != WAIT_TIMEOUT)
				return waited;
		}
	}

private:
	std::vector<HWND> disabledWindows;
	static BOOL CALLBACK disableWindow (HWND window, LPARAM parameter) {
		if (IsWindowVisible (window) && IsWindowEnabled (window)) {
			auto *self = reinterpret_cast <praat_python_WindowsWait *> (parameter);
			self -> disabledWindows.push_back (window);
			EnableWindow (window, FALSE);
		}
		return TRUE;
	}
};
#endif
#endif
