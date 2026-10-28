/* Exercise the Python script editor with real windows, real files and the real quit dialog.
 *
 * Environment:
 *   PRAAT_PYEDITOR_TEST_STAGE   directory for the files of this run (required)
 *   PRAAT_PYEDITOR_TEST_MODE    "" runs the in-process checks; "quit" runs the save-and-exit check
 *
 * The quit dialog and the question for a file name are modal, so a helper thread posts the clicks
 * that a person would otherwise make.  Every check then reads the files that the callbacks left
 * behind, rather than only looking at whether a dialog appeared.
 */
#include "../../sys/GuiP.h"
#include "../../sys/praat.h"
#include "../../sys/praat_translate.h"
#include "../../sys/PythonScriptEditor.h"
#include "../../sys/ScriptEditor.h"
#include "../../sys/NotebookEditor.h"
#include "../../sys/praat_python.h"
#include "../../fon/Sound.h"
#include <atomic>
#include <cstdlib>
#include <cwchar>
#include <fstream>
#include <iostream>
#include <string>
#include <thread>

extern "C" void DO_Quit (UiForm sendingForm, integer narg, Stackel args, conststring32 sendingString,
		Interpreter interpreter, conststring32 invokingButtonTitle, bool isModified, void *buttonClosure,
		Editor optionalEditor);

static std::string stagePath;
static std::string runMode;

static void log (const std::string &line) {
	std::cout << line << "\n" << std::flush;
	std::ofstream file (stagePath + "/native-log.txt", std::ios::app);
	file << line << "\n";
}

static void check (bool condition, const char *what) {
	if (! condition)
		Melder_throw (U"Python editor check failed: ", Melder_peek8to32_u (what));
}

static conststring32 stageFilePath (conststring32 name) {
	return Melder_cat (Melder_peek8to32_u (stagePath.c_str ()), U"/", name);
}

static structMelderFile stageMelderFile (conststring32 name) {
	structMelderFile file { };
	Melder_pathToFile (stageFilePath (name), & file);
	return file;
}

static autostring32 textOfWidget (TextEditor me) {
	return GuiText_getString (me -> textWidget);
}

static void bindToStageFile (TextEditor me, conststring32 name) {
	structMelderFile file = stageMelderFile (name);
	MelderFile_copy (& file, & me -> file);
}

static autostring32 readStageFile (conststring32 name) {
	structMelderFile file = stageMelderFile (name);
	if (! MelderFile_readable (& file))
		return autostring32 ();
	return MelderFile_readText (& file);
}

static void writeStageFile (conststring32 name, conststring32 text) {
	structMelderFile file = stageMelderFile (name);
	MelderFile_writeText_e (& file, text, Melder_getOutputEncoding ());
}

static PythonScriptEditor openPythonEditor (conststring32 initialText) {
	autoPythonScriptEditor me = PythonScriptEditor_createFromText (initialText);
	PythonScriptEditor editor = me.get ();
	me. releaseToUser ();
	return editor;
}

/**
	Type into the native edit control, which is how text reaches the widget and its change callback.
	Setting the text of the widget programmatically does not do that, so it is not used here.
*/
static void typeIntoEditor (TextEditor me, conststring32 text) {
	HWND edit = me -> textWidget -> d_widget -> window;
	check (edit, "the editor has a native text window");
	SetFocus (edit);
	for (const char32 *character = text; * character != U'\0'; character ++)
		SendMessageW (edit, WM_CHAR, (WPARAM) * character, 0);   // the text of this test stays in the basic plane
}

/**
	An editor that holds text which is not on disk: TextEditor_init starts clean, so typing is what
	makes a window unsaved.
*/
static PythonScriptEditor openUnsavedPythonEditor (conststring32 text) {
	PythonScriptEditor me = openPythonEditor (U"");
	typeIntoEditor (me, text);
	return me;
}

static integer numberOfOpenPythonEditors () {
	return theReferencesToAllOpenPythonScriptEditors.size;
}

/**
	Save through the normal route, so that a later check starts from "nothing unsaved".  A file name
	is always applied, because an earlier case may have tied this editor to a path that cannot be
	written.
*/
static void saveEditorClean (PythonScriptEditor me, conststring32 name) {
	if (name)
		bindToStageFile (me, name);
	check (TextEditor_saveOrAskSaveAs (me), "an editor tied to a file of this run can be saved");
	check (! me -> dirty, "the editor is clean after that save");
}

/*
	Clicking a button of a modal dialog, and closing the file selector that such a click opens.
*/

static wchar_t wantedCaption [256];
static HWND foundButton;

static BOOL CALLBACK eachChildWindow (HWND window, LPARAM) {
	wchar_t className [32];
	GetClassNameW (window, className, 32);
	if (lstrcmpiW (className, L"button") != 0)
		return TRUE;
	wchar_t caption [256];
	GetWindowTextW (window, caption, 256);
	std::wstring text (caption), unescaped;   /* _GuiWin_expandAmpersands doubles every "&" */
	for (size_t i = 0; i < text.size (); ) {
		if (text [i] == L'&' && i + 1 < text.size () && text [i + 1] == L'&') {
			unescaped += L'&';
			i += 2;
		} else {
			unescaped += text [i];
			i += 1;
		}
	}
	if (unescaped == wantedCaption) {
		foundButton = window;
		return FALSE;
	}
	return TRUE;
}

static BOOL CALLBACK eachTopWindow (HWND window, LPARAM) {
	unsigned long processId = 0;
	GetWindowThreadProcessId (window, & processId);
	if (processId == GetCurrentProcessId ())
		EnumChildWindows (window, eachChildWindow, 0);
	return foundButton == nullptr;
}

static HWND findButton (const wchar_t *caption, int milliseconds) {
	foundButton = nullptr;
	wcscpy (wantedCaption, caption);
	for (int elapsed = 0; elapsed < milliseconds; elapsed += 25) {
		EnumWindows (eachTopWindow, 0);
		if (foundButton)
			return foundButton;
		Sleep (25);
	}
	return nullptr;
}

static const char *probePath = nullptr;
static const wchar_t *wantedTitlePart = nullptr;
static HWND foundNativeWindow;
static wchar_t foundNativeTitle [256];

/**
	Native window titles are UTF-16; `char32` is 32 bits, so they are not the same thing here.
*/
static std::string narrowOfWide (const wchar_t *text) {
	int length = WideCharToMultiByte (CP_UTF8, 0, text, -1, nullptr, 0, nullptr, nullptr);
	if (length <= 1)
		return std::string ();
	std::string text8 (size_t (length - 1), '\0');
	WideCharToMultiByte (CP_UTF8, 0, text, -1, text8.data (), length, nullptr, nullptr);
	return text8;
}

/**
	The native question for a file name and the native Message box are both window class #32770, and
	Praat's own dialogs have other classes, so a title tells the two native kinds apart.  Before
	answering, the helper reads the script file; that is how the order "project file first, scripts
	afterwards" becomes observable.
*/
static BOOL CALLBACK eachNativeWindow (HWND window, LPARAM) {
	unsigned long processId = 0;
	GetWindowThreadProcessId (window, & processId);
	wchar_t className [32];
	GetClassNameW (window, className, 32);
	if (processId != GetCurrentProcessId () || lstrcmpW (className, L"#32770") != 0 || ! IsWindowVisible (window))
		return TRUE;
	wchar_t title [256];
	GetWindowTextW (window, title, 256);
	if (! std::wcsstr (title, wantedTitlePart))
		return TRUE;
	foundNativeWindow = window;
	wcsncpy (foundNativeTitle, title, 255);
	foundNativeTitle [255] = L'\0';
	return FALSE;
}

/**
	Press the button of a native question, or close it if that button is not there (the modern file
	selector has no child button with the classic identifier).  Returns whether the question went away.
*/
static bool answerNativeWindow (const wchar_t *titlePart, int buttonId, int milliseconds) {
	wantedTitlePart = titlePart;
	for (int elapsed = 0; elapsed < milliseconds; elapsed += 50) {
		foundNativeWindow = nullptr;
		EnumWindows (eachNativeWindow, 0);
		if (! foundNativeWindow) {
			Sleep (50);
			continue;
		}
		log ("helper: the native question on the screen is titled: " + narrowOfWide (foundNativeTitle));
		if (probePath) {
			std::ifstream file (probePath);
			std::string content ((std::istreambuf_iterator <char> (file)), std::istreambuf_iterator <char> ());
			log ("helper: while the question for a file name was open, the script file held: " + content);
		}
		HWND button = GetDlgItem (foundNativeWindow, buttonId);
		if (button)
			PostMessageW (foundNativeWindow, WM_COMMAND, MAKEWPARAM (buttonId, BN_CLICKED), (LPARAM) button);
		else
			PostMessageW (foundNativeWindow, WM_CLOSE, 0, 0);
		for (int waited = 0; waited < 3000; waited += 50) {
			if (! IsWindow (foundNativeWindow) || ! IsWindowVisible (foundNativeWindow))
				return true;
			Sleep (50);
		}
		PostMessageW (foundNativeWindow, WM_CLOSE, 0, 0);   // the first answer did not reach it
		for (int waited = 0; waited < 3000; waited += 50) {
			if (! IsWindow (foundNativeWindow) || ! IsWindowVisible (foundNativeWindow))
				return true;
			Sleep (50);
		}
		return false;
	}
	return false;
}

struct DialogHelper {
	std::atomic<bool> finished;
	std::thread thread;
	/**
		`caption` is a button of a Praat dialog; `nativeTitlePart` and `nativeButtonId` are a native
		question that this action opens (a file selector or a Message box).  Either may be null.
	*/
	DialogHelper (const wchar_t *caption, const wchar_t *nativeTitlePart, int nativeButtonId, const char *probe) :
		finished (false),
		thread ([this, caption, nativeTitlePart, nativeButtonId, probe] {
			if (caption) {
				HWND button = findButton (caption, 8000);
				if (button) {
					PostMessageW (GetParent (button), WM_COMMAND,
							MAKEWPARAM (GetDlgCtrlID (button), BN_CLICKED), (LPARAM) button);
				} else {
					log ("helper: this button did not appear; the check cannot continue");
				}
			}
			if (nativeTitlePart) {
				probePath = probe;
				const bool answered = answerNativeWindow (nativeTitlePart, nativeButtonId, 10000);
				log (answered ? std::string ("helper: the native question was answered")
						: std::string ("helper: FAILED, a native question stayed on the screen"));
			}
			finished = true;
		})
	{}
	/**
		Wait for the helper, while feeding the window procedures.  The click that the helper posts can
		only be carried out by this thread, and finding the button needs this thread to answer
		`GetWindowTextW`; waiting without pumping would deadlock.
	*/
	void join (integer milliseconds) {
		for (integer elapsed = 0; elapsed < milliseconds && ! finished.load (); elapsed += 10) {
			XEvent message;
			if (PeekMessage (& message, nullptr, 0, 0, PM_REMOVE))
				XtDispatchEvent (& message);
			else
				Sleep (10);
		}
		if (! finished.load ()) {
			log ("helper: FAILED, the helper was still busy");
			thread.detach ();   // the check reports the failure; the thread must not end the process
			return;
		}
		thread.join ();
	}
};

/**
	The helper acts once per native question, so a check that expects another question needs a new helper.
*/
static DialogHelper askForDialogAction (const wchar_t *caption, const wchar_t *nativeTitlePart,
	int nativeButtonId, const char *probe) {
	return DialogHelper (caption, nativeTitlePart, nativeButtonId, probe);
}

/**
	The click that the helper posts is an ordinary window message; it reaches the callback of the
	button only when someone feeds it to the window procedure.  `GuiMainLoop` does that forever, so
	here the same dispatch runs for a bounded time and the check can report a button that never acts.
*/
static void pumpMessages (integer milliseconds) {
	for (integer elapsed = 0; elapsed < milliseconds; elapsed += 10) {
		XEvent message;
		if (PeekMessage (& message, nullptr, 0, 0, PM_REMOVE))
			XtDispatchEvent (& message);   // the window procedures act on this thread
		else
			Sleep (10);
	}
}

/*
	Checks
*/

static void checkRegistryAndDirtyState () {
	check (numberOfOpenPythonEditors () == 0, "no Python editor is open at the start");
	check (! PythonScriptEditors_dirty (), "an empty registry does not report unsaved work");
	check (! ScriptEditors_dirty (), "no Praat script is open, so ScriptEditors_dirty is false");
	check (! NotebookEditors_dirty (), "no notebook is open, so NotebookEditors_dirty is false");
	{
		autoPythonScriptEditor me = PythonScriptEditor_createFromText (U"");
		check (numberOfOpenPythonEditors () == 1, "an open editor is in the registry");
		check (! me -> dirty, "a new empty editor is not dirty");
		typeIntoEditor (me.get (), U"x = 1");
		check (me -> dirty, "typing text marks the editor dirty");
		check (PythonScriptEditors_dirty (), "the dirty query reports that editor");
	}
	check (numberOfOpenPythonEditors () == 0, "a destroyed editor is out of the registry");
	check (! PythonScriptEditors_dirty (), "a destroyed editor does not keep the dirty state");
	log ("PASS the registry and the dirty query follow the open editors");
}

static void checkTemplatePolicy () {
	const autostring32 templateText = praat_python_getScriptTemplate ();
	check (templateText && templateText.get () [0] != U'\0', "there is a Python template to insert");
	/* An editor with no file, no text and nothing unsaved is filled in place. */
	integer before = numberOfOpenPythonEditors ();
	PythonScriptEditor empty = openPythonEditor (U"");
	check (numberOfOpenPythonEditors () == before + 1, "this case has one editor of its own");
	Editor_doMenuCommand (empty, U"Insert Praat Python template", 0, nullptr, nullptr, nullptr);
	check (numberOfOpenPythonEditors () == before + 1, "an empty editor is filled without opening another window");
	check (Melder_equ (textOfWidget (empty).get (), templateText.get ()), "the template is in the editor");
	check (empty -> dirty, "template text that is not on disk leaves the editor dirty");
	/* Text that is already there is not replaced: a second window opens instead. */
	Editor_doMenuCommand (empty, U"Insert Praat Python template", 0, nullptr, nullptr, nullptr);
	check (numberOfOpenPythonEditors () == before + 2, "existing text is kept and the template comes in a new window");
	check (theReferencesToAllOpenPythonScriptEditors.at [numberOfOpenPythonEditors ()] -> dirty,
			"the template in a new window is unsaved");
	check (Melder_equ (textOfWidget (empty).get (), templateText.get ()), "the first editor still holds its own text");
	/* An editor whose text is on disk is not overwritten either, dirty or not. */
	writeStageFile (U"template-policy.py", U"print (1)");
	before = numberOfOpenPythonEditors ();
	PythonScriptEditor onDisk = openPythonEditor (U"print (1)");
	bindToStageFile (onDisk, U"template-policy.py");
	check (! onDisk -> dirty, "an editor whose text is on disk and unchanged is clean");
	Editor_doMenuCommand (onDisk, U"Insert Praat Python template", 0, nullptr, nullptr, nullptr);
	check (numberOfOpenPythonEditors () == before + 2, "the template of a file-based editor opens in a new window");
	check (Melder_equ (textOfWidget (onDisk).get (), U"print (1)"), "the file-based editor keeps its own text");
	check (Melder_equ (readStageFile (U"template-policy.py").get (), U"print (1)"), "the file on disk is untouched");
	/* Typed text that was never saved is likewise not thrown away. */
	before = numberOfOpenPythonEditors ();
	PythonScriptEditor unsaved = openUnsavedPythonEditor (U"user typed this");
	Editor_doMenuCommand (unsaved, U"Insert Praat Python template", 0, nullptr, nullptr, nullptr);
	check (numberOfOpenPythonEditors () == before + 2, "unsaved text moves the template to a new window");
	check (Melder_equ (textOfWidget (unsaved).get (), U"user typed this"), "the typed text is still there");
	/* The same policy holds for the examples. */
	Editor_doMenuCommand (unsaved, U"Example: Batch Pitch & Formants", 0, nullptr, nullptr, nullptr);
	check (numberOfOpenPythonEditors () == before + 3, "an example also opens a new window");
	check (Melder_equ (textOfWidget (unsaved).get (), U"user typed this"), "an example does not replace typed text");
	/*
		Every window that these cases opened holds text that is not on disk.  The checks below ask the
		whole program about unsaved work, so all of them are saved first.
	*/
	saveEditorClean (empty, U"template-policy-1.py");
	saveEditorClean (onDisk, U"template-policy.py");
	saveEditorClean (unsaved, U"template-policy-2.py");
	for (integer i = 1; i <= theReferencesToAllOpenPythonScriptEditors.size; i ++) {
		const PythonScriptEditor extra = theReferencesToAllOpenPythonScriptEditors.at [i];
		if (MelderFile_isNull (& extra -> file)) {
			check (extra -> dirty, "a new template or example window has text to save");
			char32 name [30];
			Melder_sprint (name, 30, U"template-window-", i, U".py");   // not Melder_cat: this text outlives the call
			bindToStageFile (extra, name);
		}
		const autostring32 expectedText = textOfWidget (extra);
		check (TextEditor_saveOrAskSaveAs (extra), "the window that this case opened can be saved");
		check (MelderFile_readable (& extra -> file), "saving the template or example creates its file");
		const autostring32 savedText = MelderFile_readText (& extra -> file);
		check (Melder_equ (savedText.get (), expectedText.get ()), "the whole template or example is on disk");
	}
	check (! PythonScriptEditors_dirty (), "nothing of this case is left unsaved");
	log ("PASS the template and the examples keep existing text and empty editors are filled in place");
}

static void checkSave () {
	const conststring32 text = U"syntax = 1";
	PythonScriptEditor me = openUnsavedPythonEditor (text);
	bindToStageFile (me, U"saved.py");
	check (me -> dirty, "new text is dirty before the save");
	check (TextEditor_saveOrAskSaveAs (me), "an editor with a file name saves without asking");
	check (! me -> dirty, "the editor is clean after the save");
	check (! PythonScriptEditors_dirty (), "nothing is unsaved after the save");
	check (Melder_equ (readStageFile (U"saved.py").get (), text), "the file on disk holds the text of the editor");
	check (TextEditor_saveOrAskSaveAs (me), "a clean editor has nothing to save");
	/* A file that cannot be written must stay reported as unsaved work, and the user must be told. */
	PythonScriptEditor broken = openUnsavedPythonEditor (U"changed again");
	structMelderFile nowhere { };
	Melder_pathToFile (Melder_cat (Melder_peek8to32_u (stagePath.c_str ()), U"/no-such-folder/script.py"), & nowhere);
	MelderFile_copy (& nowhere, & broken -> file);
	{
		DialogHelper helper = askForDialogAction (nullptr, L"Message", IDOK, nullptr);
		check (! TextEditor_saveOrAskSaveAs (broken), "a file that cannot be written is reported as unsaved");
		helper.join (20000);
	}
	check (broken -> dirty, "the editor keeps its unsaved state after a failed write");
	check (PythonScriptEditors_dirty (), "the quit question is still needed after a failed write");
	/* Cancelling the question for a file name must not count as saving. */
	PythonScriptEditor untitled = openUnsavedPythonEditor (U"needs a name");
	structMelderFile previousAnswer = stageMelderFile (U"untitled.py");
	MelderFile_delete (& previousAnswer);
	{
		DialogHelper helper = askForDialogAction (nullptr, L"Save", IDCANCEL, nullptr);
		check (! TextEditor_saveOrAskSaveAs (untitled), "a cancelled file name is not a saved editor");
		helper.join (20000);
	}
	check (untitled -> dirty, "the editor is still dirty after the cancelled question");
	check (readStageFile (U"untitled.py").get () == nullptr, "no file is written when the question is cancelled");
	check (PythonScriptEditors_dirty (), "a cancelled save keeps the quit question open");
	saveEditorClean (broken, U"broken.py");
	saveEditorClean (untitled, U"untitled.py");
	check (! PythonScriptEditors_dirty (), "every editor of this check is saved");
	log ("PASS saving writes the text, and a cancelled or failed save stays unsaved");
}

static void checkQuitDialogKeepsWorkOnCancel () {
	check (! PythonScriptEditors_dirty (), "every editor of the earlier checks is saved");
	PythonScriptEditor me = openUnsavedPythonEditor (U"unsaved text");
	check (PythonScriptEditors_dirty (), "this editor is the reason for the quit question");
	{
		DialogHelper helper = askForDialogAction (L"Cancel", nullptr, 0, nullptr);
		DO_Quit (nullptr, 0, nullptr, nullptr, nullptr, nullptr, false, nullptr, nullptr);
		helper.join (20000);   // the click of the helper is carried out while we wait for it
		pumpMessages (1000);   // the callback of that click has returned by now
	}
	check (PythonScriptEditors_dirty (), "Cancel aborts the exit and keeps the work unsaved");
	check (me -> dirty, "Cancel does not touch the unsaved text");
	check (readStageFile (U"cancelled-quit.py").get () == nullptr, "Cancel writes no file");
	saveEditorClean (me, U"cancelled-quit.py");
	check (! PythonScriptEditors_dirty (), "after saving there is nothing left to ask about");
	log ("PASS Cancel in the quit dialog leaves the program running with its work unsaved");
}

static void checkProjectFileComesFirst () {
	/*
		With objects in the list, "Save Project (.praat)" asks for a project file before it saves any
		script.  Cancelling that first question must therefore leave the script exactly as it was.
	*/
	writeStageFile (U"project-first.py", U"ORIGINAL");
	praat_new (Sound_createSimple (1, 0.01, 22050), U"quitTest");
	PythonScriptEditor me = openUnsavedPythonEditor (U"print ('not yet saved')");
	bindToStageFile (me, U"project-first.py");
	check (me -> dirty, "the script is unsaved when the dialog is opened");
	static std::string probe = stagePath + "/project-first.py";
	{
		DialogHelper helper = askForDialogAction (L"Save Project (.praat)", L"workspace", IDCANCEL, probe.c_str ());
		DO_Quit (nullptr, 0, nullptr, nullptr, nullptr, nullptr, false, nullptr, nullptr);
		helper.join (25000);   // the click opens the question for a file name, the helper answers it
		pumpMessages (1000);
	}
	check (me -> dirty, "cancelling the project question aborts the script save as well");
	check (Melder_equ (readStageFile (U"project-first.py").get (), U"ORIGINAL"), "the script file is unchanged on disk");
	check (theCurrentPraatObjects -> n == 1, "the object is still in the list");
	check (PythonScriptEditors_dirty (), "the program is still asking to save");
	saveEditorClean (me, nullptr);
	while (theCurrentPraatObjects -> n > 0)
		praat_removeObject (theCurrentPraatObjects -> n);
	log ("PASS the project file is asked first and cancelling it saves nothing");
}

/**
	An empty list needs no project file: the button then saves the scripts and quits.  Because the
	last step of the callback is the exit itself, this runs in a process of its own; the caller
	checks the file on disk and the exit status afterwards.
*/
static void checkSaveProjectAndQuit () {
	const conststring32 text = U"print ('saved by the quit dialog')";
	structMelderFile previousSave = stageMelderFile (U"saved-by-quit.py");
	MelderFile_delete (& previousSave);
	check (theCurrentPraatObjects -> n == 0, "the object list is empty");
	PythonScriptEditor me = openUnsavedPythonEditor (text);
	bindToStageFile (me, U"saved-by-quit.py");
	check (me -> dirty, "the script is unsaved when the dialog is opened");
	Editor_doMenuCommand (me, U"Insert Praat Python template", 0, nullptr, nullptr, nullptr);
	PythonScriptEditor generated = theReferencesToAllOpenPythonScriptEditors.at [numberOfOpenPythonEditors ()];
	check (generated != me && generated -> dirty, "the new template needs saving before quitting");
	check (PythonScriptEditors_dirty (), "the quit question includes the new template");
	bindToStageFile (generated, U"quit-template.py");
	writeStageFile (U"quit-template-expected.py", textOfWidget (generated).get ());
	log ("child: clicking Save Project (.praat) with an empty list");
	DialogHelper helper = askForDialogAction (L"Save Project (.praat)", nullptr, 0, nullptr);
	DO_Quit (nullptr, 0, nullptr, nullptr, nullptr, nullptr, false, nullptr, nullptr);
	helper.join (20000);   // the callback of that button saves the script and ends this process
	pumpMessages (8000);   // unless the callback never came: then this check reports itself
	log ("child: FAILED, this process was still alive after the click");
	exit (3);
}

int main (int argc, char **argv) {
	try {
		if (const char *stage = std::getenv ("PRAAT_PYEDITOR_TEST_STAGE"))
			stagePath = stage;
		if (const char *selectedMode = std::getenv ("PRAAT_PYEDITOR_TEST_MODE"))
			runMode = selectedMode;
		if (stagePath.empty ())
			Melder_throw (U"PRAAT_PYEDITOR_TEST_STAGE is not set.");
		praat_init (U"PythonEditorTest", U"1.0", 1000, 2026, 10, 20, U"test", U"example.invalid", argc, argv);
		INCLUDE_LIBRARY (praat_uvafon_init)
		/*
			The interface language is a preference, and registering the preferences sets it to its
			default.  English is needed here because the checks find the buttons of the quit dialog by
			the titles that the source code gives them.
		*/
		g_language_choice = 0;
		if (runMode == "quit") {
			checkSaveProjectAndQuit ();
			return 4;   /* not reached: the quit callback ends the process */
		}
		checkRegistryAndDirtyState ();
		checkTemplatePolicy ();
		checkSave ();
		checkQuitDialogKeepsWorkOnCancel ();
		checkProjectFileComesFirst ();
		log ("all Python script editor checks passed");
		return 0;
	} catch (MelderError) {
		log (std::string ("FAILED: ") + Melder_peek32to8 (Melder_getError ()));
		Melder_clearError ();
		return 1;
	}
}
