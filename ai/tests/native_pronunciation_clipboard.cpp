/* Exercise the actual editor renderer and clipboard publisher without UI input. */
#include <iostream>
#include <filesystem>
#include <fstream>
#include "../../foned/SoundEditor.h"
#include "../../sys/praat.h"
#include "../../foned/FunctionEditor.cpp"

int main (int argc, char **argv) {
	try {
		praat_init (U"ClipboardTest", U"1.0", 1000, 2026, 10, 19, U"test", U"example.invalid", argc, argv);
		INCLUDE_LIBRARY (praat_uvafon_init)
		autoSound sound = Sound_createSimple (1, 1.17542, 44100);
		Thing_setName (sound.get(), U"测试発音&<练习>");
		for (integer i = 1; i <= sound -> nx; i ++) {
			const double time = (i - 0.5) / 44100.0;
			sound -> z [1] [i] = 0.3 * sin (2 * NUMpi * (180 * time + 80 * time * time));
		}
		autoSoundEditor editor = SoundEditor_create (U"Clipboard renderer test", sound.get());
		HMENU menu = editor -> aiFrontendMenu -> d_widget -> nat.menu.handle;
		const wchar_t *labels [] = { L"复制发音截图", L"复制提问文字", L"复制截图和提问（图文）" };
		for (int i = 0; i < 3; i ++) {
			wchar_t item [128] {};
			GetMenuStringW (menu, GetMenuItemCount (menu) - 3 + i, item, 128, MF_BYPOSITION);
			Melder_assert (wcscmp (item, labels [i]) == 0);
		}
		conststring32 mode = Melder_getenv (U"PRAAT_CLIPBOARD_TEST_MODE");
		const bool questionOnly = mode && str32equ (mode, U"question");
		const bool combined = mode && str32equ (mode, U"combined");
		editor -> startWindow = 0.156642;
		editor -> endWindow = 1.023594;
		editor -> soundAnalysisArea() -> setInstancePref_spectrogram_show (true);
		editor -> soundAnalysisArea() -> setInstancePref_pitch_show (true);
		editor -> soundAnalysisArea() -> setInstancePref_formant_show (true);
		if (Melder_getenv (U"PRAAT_CLIPBOARD_TEST_VIEWER")) {
			editor -> setInstancePref_showSelectionViewer (true);
			editor -> updateGeometry (GuiControl_getWidth (editor -> drawingArea), GuiControl_getHeight (editor -> drawingArea));
		}
		HDC original = ((GraphicsScreen) editor -> graphics.get()) -> d_gdiGraphicsContext;
		HWND root = GetAncestor (((GraphicsScreen) editor -> graphics.get()) -> d_winWindow, GA_ROOT);
		ShowWindow (root, SW_SHOWNORMAL);
		SetWindowPos (root, HWND_TOP, 40, 40, 1000, 700, SWP_SHOWWINDOW);
		SetForegroundWindow (root);
		FunctionEditor_redraw (editor.get());
		RedrawWindow (root, nullptr, nullptr, RDW_INVALIDATE | RDW_UPDATENOW | RDW_ALLCHILDREN);
		if (questionOnly) {
			aiCopyPronunciationCallback (editor.get(), nullptr);
			aiCopyQuestionCallback (editor.get(), nullptr);
			Melder_assert (pendingScreenshotEditors.empty());
			Melder_assert (IsClipboardFormatAvailable (CF_UNICODETEXT));
			Melder_assert (! IsClipboardFormatAvailable (CF_DIB));
			std::cout << "question copied immediately; pending screenshot cancelled\n";
			return 0;
		}
		const DWORD clipboardBefore = GetClipboardSequenceNumber ();
		const ULONGLONG captureStarted = GetTickCount64 ();
		for (int i = 0; i < 2; i ++) {
			if (combined)
				aiCopyScreenshotAndQuestionCallback (editor.get(), nullptr);
			else
				aiCopyPronunciationCallback (editor.get(), nullptr);
		}
		Melder_assert (pendingScreenshotEditors.size() == 1);
		Melder_assert (GetClipboardSequenceNumber () == clipboardBefore);
		while (! pendingScreenshotEditors.empty() && GetTickCount64 () - captureStarted < 2000) {
			MSG message;
			while (PeekMessageW (& message, nullptr, 0, 0, PM_REMOVE)) {
				TranslateMessage (& message);
				DispatchMessageW (& message);
			}
			Sleep (1);
		}
		Melder_assert (pendingScreenshotEditors.empty());
		Melder_assert (GetClipboardSequenceNumber () != clipboardBefore);
		Melder_assert (GetTickCount64 () - captureStarted >= 450);
		std::cout << "native screen capture completed after " << GetTickCount64 () - captureStarted << " ms\n";
		Melder_assert (((GraphicsScreen) editor -> graphics.get()) -> d_gdiGraphicsContext == original);
		if (conststring32 output = Melder_getenv (U"PRAAT_CLIPBOARD_TEST_TOAST_DIR")) {
			const auto directory = std::filesystem::u8path (Melder_peek32to8 (output));
			for (UINT dpi : { 96, 120, 144, 168, 192, 216, 240, 288, 336, 384 }) {
				const auto image = renderCopiedToast (dpi);
				std::ofstream file (directory / ("toast-" + std::to_string (dpi) + ".bgra"), std::ios::binary);
				file.write ((const char *) image.pixels.data(), image.pixels.size() * sizeof (uint32_t));
				Melder_assert (file.good());
				std::cout << "rendered " << dpi << " dpi: " << image.width << " x " << image.height << "\n";
			}
		}
		Melder_assert (((GraphicsScreen) editor -> graphics.get()) -> d_gdiGraphicsContext == original);
		HWND foreground = GetForegroundWindow ();
		const ULONGLONG started = GetTickCount64 ();
		HWND toast = showCopiedToast (((GraphicsScreen) editor -> graphics.get()) -> d_winWindow);
		Melder_assert (toast && IsWindowVisible (toast));
		Melder_assert (GetForegroundWindow () == foreground);
		Melder_assert (GetWindowLongPtrW (toast, GWL_EXSTYLE) & WS_EX_LAYERED);
		Melder_assert (IsWindowEnabled (GetAncestor (((GraphicsScreen) editor -> graphics.get()) -> d_winWindow, GA_ROOT)));
		while (IsWindow (toast) && GetTickCount64 () - started < 1500) {
			MSG message;
			while (PeekMessageW (& message, nullptr, 0, 0, PM_REMOVE)) {
				TranslateMessage (& message);
				DispatchMessageW (& message);
			}
			Sleep (1);
		}
		const ULONGLONG duration = GetTickCount64 () - started;
		Melder_assert (! IsWindow (toast) && duration >= 350 && duration < 1000);
		std::cout << "non-activating translucent toast dismissed after " << duration << " ms\n";
		std::cout << "copied current zoom with Chinese/Japanese and HTML-special filename\n";
		return 0;
	} catch (MelderError) {
		std::cerr << Melder_peek32to8 (Melder_getError()) << "\n";
		Melder_clearError ();
		return 1;
	}
}
