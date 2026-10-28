#ifndef _praat_python_wait_h_
#define _praat_python_wait_h_

#if defined (_WIN32)
#include <windows.h>
#include <commctrl.h>
#include <algorithm>
#include <stdexcept>
#include <vector>

// Python owns a snapshot of the selected objects and the process workspace.
// Keep client controls and application callbacks idle while the native frames
// remain movable. The system move/size loop must obey the same callback boundary.
class praat_python_WindowsWait {
public:
	praat_python_WindowsWait () {
		messageHook = SetWindowsHookExW (WH_GETMESSAGE, filterRetrievedMessage, nullptr, GetCurrentThreadId ());
		if (! messageHook)
			throw std::runtime_error ("Cannot guard the Python wait message loop.");
		previousWait = currentWait;
		currentWait = this;
		EnumThreadWindows (GetCurrentThreadId (), prepareWindow, reinterpret_cast <LPARAM> (this));
	}
	~praat_python_WindowsWait () {
		UnhookWindowsHookEx (messageHook);
		currentWait = previousWait;
		for (HWND window : frames)
			if (IsWindow (window))
				RemoveWindowSubclass (window, guardFrame, reinterpret_cast <UINT_PTR> (this));
		for (HWND window : disabledWindows)
			if (IsWindow (window))
				EnableWindow (window, TRUE);
		for (const MSG & message : deferredMessages)
			if (! message.hwnd || IsWindow (message.hwnd)) {
				if (message.hwnd)
					PostMessageW (message.hwnd, message.message, message.wParam, message.lParam);
				else
					PostThreadMessageW (GetCurrentThreadId (), message.message, message.wParam, message.lParam);
			}
	}
	praat_python_WindowsWait (const praat_python_WindowsWait &) = delete;
	praat_python_WindowsWait & operator= (const praat_python_WindowsWait &) = delete;

	void pump () const {
		MSG message;
		// Peek also services sent messages and resets the hung-window watchdog.
		// Leave application commands in place for the normal event loop.
		PeekMessageW (& message, nullptr, 0, 0, PM_NOREMOVE);
		while (PeekMessageW (& message, nullptr, WM_PAINT, WM_PAINT, PM_REMOVE))
			DispatchMessageW (& message);
		while (PeekMessageW (& message, nullptr, WM_NCMOUSEMOVE, WM_NCXBUTTONDBLCLK, PM_REMOVE))
			DispatchMessageW (& message);
		while (PeekMessageW (& message, nullptr, WM_SYSCOMMAND, WM_SYSCOMMAND, PM_REMOVE))
			DispatchMessageW (& message);
		// Client input received while a frame is active must not become an edit
		// after the Python snapshot has finished.
		for (HWND frame : frames) {
			while (PeekMessageW (& message, frame, WM_KEYFIRST, WM_KEYLAST, PM_REMOVE)) { }
			while (PeekMessageW (& message, frame, WM_MOUSEFIRST, WM_MOUSELAST, PM_REMOVE)) { }
		}
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
	inline static thread_local praat_python_WindowsWait *currentWait = nullptr;
	praat_python_WindowsWait *previousWait = nullptr;
	HHOOK messageHook = nullptr;
	std::vector<HWND> frames;
	std::vector<HWND> disabledWindows;
	std::vector<MSG> deferredMessages;

	static bool applicationMessage (UINT message) {
		return message == WM_COMMAND || message == WM_TIMER || message == WM_CLOSE ||
			message == WM_DROPFILES || message >= WM_USER;
	}
	bool ownsWindow (HWND window) const {
		if (! window)
			return true; // Thread timers also run application code.
		for (HWND frame : frames)
			if (frame == window || IsChild (frame, window))
				return true;
		return false;
	}
	static LRESULT CALLBACK filterRetrievedMessage (int code, WPARAM removed, LPARAM parameter) {
		if (code >= 0 && removed == PM_REMOVE && currentWait) {
			auto & message = *reinterpret_cast <MSG *> (parameter);
			if (applicationMessage (message.message) && currentWait -> ownsWindow (message.hwnd)) {
				auto & deferred = currentWait -> deferredMessages;
				const bool repeatedTimer = message.message == WM_TIMER && std::any_of (deferred.begin (), deferred.end (),
					[&] (const MSG & previous) { return previous.hwnd == message.hwnd && previous.message == WM_TIMER && previous.wParam == message.wParam; });
				if (! repeatedTimer)
					deferred.push_back (message);
				message.message = WM_NULL;
				message.wParam = 0;
				message.lParam = 0;
			}
		}
		return CallNextHookEx (nullptr, code, removed, parameter);
	}
	static LRESULT CALLBACK guardFrame (HWND window, UINT message, WPARAM first, LPARAM second, UINT_PTR, DWORD_PTR) {
		// Sent commands/IPC cannot be queued safely because their payload can be
		// borrowed memory. Reject them while the exported snapshot is in use.
		if (applicationMessage (message) || message == WM_COPYDATA || message == WM_NOTIFY ||
			(message >= WM_KEYFIRST && message <= WM_KEYLAST) ||
			(message >= WM_MOUSEFIRST && message <= WM_MOUSELAST))
			return 0;
		if (message == WM_SYSCOMMAND) {
			const WPARAM command = first & 0xFFF0;
			if (command != SC_MOVE && command != SC_SIZE && command != SC_MINIMIZE &&
				command != SC_MAXIMIZE && command != SC_RESTORE)
				return 0;
		}
		if (message == WM_NCLBUTTONDOWN || message == WM_NCLBUTTONDBLCLK) {
			if (first != HTCAPTION && first != HTMINBUTTON && first != HTMAXBUTTON &&
				(first < HTLEFT || first > HTBOTTOMRIGHT))
				return 0;
		}
		if (message == WM_NCRBUTTONDOWN || message == WM_NCRBUTTONUP || message == WM_NCRBUTTONDBLCLK)
			return 0;
		return DefSubclassProc (window, message, first, second);
	}
	static BOOL CALLBACK disableControl (HWND window, LPARAM parameter) {
		if (IsWindowEnabled (window)) {
			auto *self = reinterpret_cast <praat_python_WindowsWait *> (parameter);
			self -> disabledWindows.push_back (window);
			EnableWindow (window, FALSE);
		}
		return TRUE;
	}
	static BOOL CALLBACK prepareWindow (HWND window, LPARAM parameter) {
		if (IsWindowVisible (window) && IsWindowEnabled (window)) {
			auto *self = reinterpret_cast <praat_python_WindowsWait *> (parameter);
			if (SetWindowSubclass (window, guardFrame, reinterpret_cast <UINT_PTR> (self), 0)) {
				self -> frames.push_back (window);
				EnumChildWindows (window, disableControl, parameter);
			} else {
				self -> disabledWindows.push_back (window);
				EnableWindow (window, FALSE);
			}
		}
		return TRUE;
	}
};
#endif
#endif
