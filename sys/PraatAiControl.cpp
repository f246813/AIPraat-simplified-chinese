/* PraatAiControl.cpp
 *
 * Local AI frontend integration for Praat.
 */

#include "PraatAiControl.h"
#include "GuiP.h"
#include "machine.h"
#include "praat.h"
#include "praat_python.h"
#include "praat_translate.h"
#include "Preferences.h"
#include <algorithm>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <optional>
#include <sstream>
#include <string>
#include <unordered_map>
#include <vector>
#include "../external/picojson/picojson.h"

#if defined (_WIN32)
	#include <windows.h>
#else
	#include <cstdlib>
	#include <unistd.h>
#endif

namespace {
	char32 theAiProjectDirectory [Preferences_STRING_BUFFER_SIZE];
	char32 theAiAlignmentMode [32];
	bool statusSuccess = false;
	bool statusLocalFrontend = false;
	int statusVramWarning = 0; // unavailable=0, red=1, yellow=2, green=3
	MelderString statusFrontendModel;
	MelderString statusFrontendStatus;
	char32 theAiProjectDirectoryBuffer [Preferences_STRING_BUFFER_SIZE];
	constexpr conststring32 defaultFrontendModel = U"未配置";
	/*
		用户在编辑器里手动拖出来的选区。只在「编辑器还开着」时才算数：
		编辑器关掉之后 editors[] 里的那个指针会被清成 null，这里自然就失效了。
	*/
	Thing theNotedEditor = nullptr;
	Thing theNotedEditorObject = nullptr;
	double theNotedSelectionStart = 0.0, theNotedSelectionEnd = 0.0;

	bool notedSelectionMatches (integer iobject, Daata object) {
		if (! theNotedEditor || theNotedEditorObject != (Thing) object)
			return false;
		if (! (theNotedSelectionEnd > theNotedSelectionStart))
			return false;   // 只点了一下光标，没拖选
		for (integer ieditor = 0; ieditor < praat_MAXNUM_EDITORS; ieditor ++)
			if ((Thing) theCurrentPraatObjects -> list [iobject]. editors [ieditor] == theNotedEditor)
				return true;
		return false;
	}

	std::string formatSelectionSeconds (double value) {
		std::ostringstream text;
		text << std::fixed << std::setprecision (6) << value;
		return text. str();
	}

	std::string jsonStringField (const std::string &json, const std::string &name, const std::string &fallback = "") {
		const std::string key = "\"" + name + "\"";
		const size_t keyPosition = json. find (key);
		if (keyPosition == std::string::npos)
			return fallback;
		const size_t colon = json. find (':', keyPosition + key. size());
		if (colon == std::string::npos)
			return fallback;
		const size_t quote1 = json. find ('"', colon + 1);
		if (quote1 == std::string::npos)
			return fallback;
		const size_t quote2 = json. find ('"', quote1 + 1);
		if (quote2 == std::string::npos)
			return fallback;
		return json. substr (quote1 + 1, quote2 - quote1 - 1);
	}

	bool jsonBoolField (const std::string &json, const std::string &name, bool fallback) {
		const std::string key = "\"" + name + "\"";
		const size_t keyPosition = json. find (key);
		if (keyPosition == std::string::npos)
			return fallback;
		const size_t colon = json. find (':', keyPosition + key. size());
		if (colon == std::string::npos)
			return fallback;
		const size_t valueStart = json. find_first_not_of (" \t\r\n", colon + 1);
		if (valueStart == std::string::npos)
			return fallback;
		return json. compare (valueStart, 4, "true") == 0;
	}

	double jsonNumberField (const std::string &json, const std::string &name, double fallback) {
		const std::string key = "\"" + name + "\"";
		const size_t keyPosition = json. find (key);
		if (keyPosition == std::string::npos)
			return fallback;
		const size_t colon = json. find (':', keyPosition + key. size());
		if (colon == std::string::npos)
			return fallback;
		const size_t valueStart = json. find_first_not_of (" \t\r\n", colon + 1);
		if (valueStart == std::string::npos)
			return fallback;
		try {
			return std::stod (json. substr (valueStart));
		} catch (...) {
			return fallback;
		}
	}

	bool isProjectDirectory (const std::filesystem::path &directory) {
		std::error_code error;
		return std::filesystem::exists (directory / "run_ai_control.py", error) ||
			std::filesystem::exists (directory / "runtime" / "status.json", error);
	}

	std::filesystem::path projectDirectoryPath () {
		const char32 *directory = theAiProjectDirectory [0] ? theAiProjectDirectory : U"ai";
		autostring8 directory8 = Melder_32to8 (directory);
		const std::filesystem::path configuredDirectory = std::filesystem::u8path (directory8 ? directory8.get() : "ai");
		if (configuredDirectory.is_absolute())
			return configuredDirectory;
		std::error_code error;
		const std::filesystem::path currentDirectory =
			std::filesystem::current_path (error) / configuredDirectory;
		if (isProjectDirectory (currentDirectory))
			return currentDirectory;
		#if defined (_WIN32)
			wchar_t executablePath [MAX_PATH];
			const DWORD executablePathLength = GetModuleFileNameW (
				nullptr, executablePath, static_cast <DWORD> (MAX_PATH)
			);
			if (executablePathLength > 0 && executablePathLength < MAX_PATH) {
				const std::filesystem::path executableDirectory =
					std::filesystem::path (executablePath, executablePath + executablePathLength). parent_path();
				const std::filesystem::path executableRelativeDirectory =
					executableDirectory / configuredDirectory;
				if (isProjectDirectory (executableRelativeDirectory))
					return executableRelativeDirectory;
			}
		#endif
		return currentDirectory;
	}

	std::filesystem::path controlScriptPath () {
		return projectDirectoryPath() / "run_ai_control.py";
	}

	std::filesystem::path statusPath () {
		return projectDirectoryPath() / "runtime" / "status.json";
	}

	std::optional<std::string> readTextFile (const std::filesystem::path &path) {
		std::ifstream input (path, std::ios::binary);
		if (! input. is_open())
			return std::nullopt;
		return std::string (
			(std::istreambuf_iterator<char> (input)),
			std::istreambuf_iterator<char> ()
		);
	}

	void setEnvironmentUtf8 (const char *name, const std::string &value) {
		#if defined (_WIN32)
			const int nameLength = MultiByteToWideChar (CP_UTF8, 0, name, -1, nullptr, 0);
			const int valueLength = MultiByteToWideChar (
				CP_UTF8, 0, value. c_str(), static_cast <int> (value. size()), nullptr, 0
			);
			if (nameLength <= 0 || valueLength < 0)
				return;
			std::wstring wideName (nameLength, L'\0');
			std::wstring wideValue (valueLength, L'\0');
			MultiByteToWideChar (CP_UTF8, 0, name, -1, wideName. data(), nameLength);
			if (valueLength > 0)
				MultiByteToWideChar (
					CP_UTF8, 0, value. c_str(), static_cast <int> (value. size()),
					wideValue. data(), valueLength
				);
			SetEnvironmentVariableW (wideName. c_str(), wideValue. c_str());
		#else
			setenv (name, value. c_str(), 1);
		#endif
	}

	void clearEnvironmentUtf8 (const char *name) {
		#if defined (_WIN32)
			const int nameLength = MultiByteToWideChar (CP_UTF8, 0, name, -1, nullptr, 0);
			if (nameLength <= 0)
				return;
			std::wstring wideName (nameLength, L'\0');
			MultiByteToWideChar (CP_UTF8, 0, name, -1, wideName. data(), nameLength);
			SetEnvironmentVariableW (wideName. c_str(), nullptr);
		#else
			unsetenv (name);
		#endif
	}

	void runControlCommand (conststring32 command, conststring32 value = nullptr) {
		const std::string script8 = controlScriptPath(). u8string();
		const std::string directory8 = projectDirectoryPath(). u8string();
		autostring32 script32 = Melder_8to32_e (script8. c_str());
		autostring32 directory32 = Melder_8to32_e (directory8. c_str());
		autostring8 command8 = Melder_32to8 (command ? command : U"");
		autostring8 value8 = Melder_32to8 (value ? value : U"");
		setEnvironmentUtf8 (
			"PRAAT_AI_CONTROL_COMMAND",
			command8 ? command8. get() : ""
		);
		if (value8 && value8. get() [0])
			setEnvironmentUtf8 ("PRAAT_AI_CONTROL_VALUE", value8. get());
		else
			clearEnvironmentUtf8 ("PRAAT_AI_CONTROL_VALUE");
		setEnvironmentUtf8 ("PRAAT_AI_CONTROL_QUIET", "1");
		try {
			praat_runPythonScriptFile (
				script32 ? script32. get() : U"",
				directory32 ? directory32. get() : nullptr
			);
		} catch (...) {
			clearEnvironmentUtf8 ("PRAAT_AI_CONTROL_COMMAND");
			clearEnvironmentUtf8 ("PRAAT_AI_CONTROL_VALUE");
			clearEnvironmentUtf8 ("PRAAT_AI_CONTROL_QUIET");
			throw;
		}
		clearEnvironmentUtf8 ("PRAAT_AI_CONTROL_COMMAND");
		clearEnvironmentUtf8 ("PRAAT_AI_CONTROL_VALUE");
		clearEnvironmentUtf8 ("PRAAT_AI_CONTROL_QUIET");
		PraatAiControl_refreshStatus();
	}

	void setModelPath (conststring32 path) {
		PraatAiControl_refreshStatus();
		runControlCommand (U"set-model", path);
		PraatAiControl_refreshStatus();
	}

	std::string cleanContextField (conststring32 value) {
		autostring8 value8 = Melder_32to8 (value ? value : U"");
		std::string result = value8 ? value8. get() : "";
		std::replace (result. begin(), result. end(), '\t', ' ');
		std::replace (result. begin(), result. end(), '\r', ' ');
		std::replace (result. begin(), result. end(), '\n', ' ');
		return result;
	}

	long praatProcessId () {
		/*
			当前 Praat 的进程号：写进 chat_context.tsv，供对话窗口判断这份列表
			是不是正在跑的这个 Praat 写的（C5）。
		*/
		#if defined (_WIN32)
			return static_cast <long> (GetCurrentProcessId ());
		#else
			return static_cast <long> (getpid ());
		#endif
	}

	std::string buildChatContext () {
		std::ostringstream text;
		text << "id\tclass\tname\tselected\tsel_start\tsel_end\n";
		if (theCurrentPraatObjects) {
			for (integer iobject = 1; iobject <= theCurrentPraatObjects -> n; iobject ++) {
				Daata object = theCurrentPraatObjects -> list [iobject]. object;
				if (! object)
					continue;
				text
					<< theCurrentPraatObjects -> list [iobject]. id << '\t'
					<< cleanContextField (Thing_className (object)) << '\t'
					<< cleanContextField (theCurrentPraatObjects -> list [iobject]. name. get()) << '\t'
					<< (theCurrentPraatObjects -> list [iobject]. isSelected ? "1" : "0");
				if (notedSelectionMatches (iobject, object))
					text
						<< '\t' << formatSelectionSeconds (theNotedSelectionStart)
						<< '\t' << formatSelectionSeconds (theNotedSelectionEnd);
				else
					text << "\t\t";   // 没有圈选就留空两列，行数与表头保持一致
				text << '\n';
			}
		}
		return text. str();
	}

	bool writeChatContext (bool force = false) {
		static std::string previousContext;
		/*
			末尾写上自己的进程号（C5）：对话窗口看到标记就是当前这个 Praat，就
			知道这份列表是最新的，不用每条消息再投一条空脚本刷新——省一次完整
			往返（投递 + 等待），也少一次被模态窗口挡住的机会。
			对这个文件的解析会忽略这一行（praat_ai.tools.parse_object_context）。
		*/
		const std::string context = buildChatContext ()
			+ "# praat-pid=" + std::to_string (praatProcessId ()) + "\n# praat-protocol=2\n";
		if (! force && context == previousContext)
			return true;   // 选中对象没有变化时不重复写盘
		const std::filesystem::path runtimeDirectory = projectDirectoryPath() / "runtime";
		std::error_code error;
		std::filesystem::create_directories (runtimeDirectory, error);
		const auto temporaryPath = runtimeDirectory / "chat_context.tmp";
		const auto contextPath = runtimeDirectory / "chat_context.tsv";
		std::ofstream output (temporaryPath, std::ios::binary | std::ios::trunc);
		if (! output. is_open())
			return false;
		output << context;
		output. close();
		if (! output)
			return false;
		#if defined (_WIN32)
			if (! MoveFileExW (temporaryPath. c_str(), contextPath. c_str(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH))
				return false;
		#else
			std::filesystem::rename (temporaryPath, contextPath, error);
			if (error)
				return false;
		#endif
		previousContext = context;
		return true;
	}

	std::filesystem::path praatExecutablePath () {
		#if defined (_WIN32)
			wchar_t executablePath [MAX_PATH];
			const DWORD length = GetModuleFileNameW (
				nullptr, executablePath, static_cast <DWORD> (MAX_PATH)
			);
			if (length > 0 && length < MAX_PATH)
				return std::filesystem::path (executablePath, executablePath + length);
		#endif
		return { };
	}

	#if defined (_WIN32)
		HANDLE pathSettingsProcess = nullptr;
		DWORD pathSettingsPid = 0;
		UINT_PTR pathSettingsTimer = 0;
		std::filesystem::path pathSettingsResult;

		void applyPathSettingsResult () {
			if (pathSettingsResult.empty())
				return;
			const auto result = readTextFile (pathSettingsResult);
			if (! result || result -> empty())
				return;
			autostring32 python = Melder_8to32_e (result -> c_str());
			if (python && python [0]) {
				// Update the registered preference in memory before Praat writes it on exit.
				praat_python_setExecutablePath (python. get());
				setEnvironmentUtf8 ("PRAAT_PYTHON_EXECUTABLE", *result);
			}
			std::error_code error;
			std::filesystem::remove (pathSettingsResult, error);
			pathSettingsResult.clear();
		}

		void CALLBACK pathSettingsTimerCallback (HWND /* window */, UINT /* message */, UINT_PTR timer, DWORD /* time */) {
			if (! pathSettingsProcess) {
				KillTimer (nullptr, timer);
				pathSettingsTimer = 0;
				return;
			}
			applyPathSettingsResult ();
			if (WaitForSingleObject (pathSettingsProcess, 0) == WAIT_OBJECT_0) {
				applyPathSettingsResult ();
				CloseHandle (pathSettingsProcess);
				pathSettingsProcess = nullptr;
				pathSettingsPid = 0;
				pathSettingsResult.clear();
				KillTimer (nullptr, timer);
				pathSettingsTimer = 0;
				return;
			}
		}

		BOOL CALLBACK foregroundPathSettings (HWND window, LPARAM wantedPid) {
			DWORD pid = 0;
			GetWindowThreadProcessId (window, & pid);
			if (pid != static_cast <DWORD> (wantedPid) || ! IsWindowVisible (window))
				return TRUE;
			ShowWindow (window, SW_RESTORE);
			SetForegroundWindow (window);
			return FALSE;
		}
	#endif

	/*
		跑一个「脱离」的 Python 启动器：Praat 只等它几百毫秒（它把真正的窗口进程
		拉起来就退出了）。

		顺便告诉那个窗口「是谁启动了你」——Praat 的进程号 + Praat.exe 路径。前端
		按这两条线索盯着：Praat 关掉之后，对话窗口和 API 配置小窗都自己退出
		（见 ai/praat_ai/parent_watch.py）。
	*/
	void runDetachedLauncher (const std::filesystem::path &launcher) {
		const std::string launcher8 = launcher. u8string();
		const std::string directory8 = projectDirectoryPath(). u8string();
		autostring32 launcher32 = Melder_8to32_e (launcher8. c_str());
		autostring32 directory32 = Melder_8to32_e (directory8. c_str());
		const std::filesystem::path executable = praatExecutablePath ();
		#if defined (_WIN32)
			const std::wstring pidText = std::to_wstring (praatProcessId ());
			SetEnvironmentVariableW (
				L"PRAAT_AI_PRAAT_EXECUTABLE",
				executable. empty() ? nullptr : executable. wstring(). c_str()
			);
			SetEnvironmentVariableW (L"PRAAT_AI_PRAAT_PID", pidText. c_str());
		#endif
		try {
			praat_runPythonScriptFile (
				launcher32 ? launcher32. get() : U"",
				directory32 ? directory32. get() : nullptr
			);
		} catch (...) {
			#if defined (_WIN32)
				SetEnvironmentVariableW (L"PRAAT_AI_PRAAT_EXECUTABLE", nullptr);
				SetEnvironmentVariableW (L"PRAAT_AI_PRAAT_PID", nullptr);
			#endif
			throw;
		}
		#if defined (_WIN32)
			SetEnvironmentVariableW (L"PRAAT_AI_PRAAT_EXECUTABLE", nullptr);
			SetEnvironmentVariableW (L"PRAAT_AI_PRAAT_PID", nullptr);
		#endif
	}

	void launchAiChatWindow () {
		writeChatContext (true);
		runDetachedLauncher (projectDirectoryPath() / "start_ai_chat.py");
	}

	void launchModelSettings () {
		writeChatContext (true);
		#if defined (_WIN32)
			// UI navigation needs no selected-audio export. Launch with Unicode paths,
			// using the same detached process pattern as dictionary management.
			auto project = projectDirectoryPath ();
			wchar_t configuredProject [Preferences_STRING_BUFFER_SIZE] { };
			const DWORD projectLength = GetEnvironmentVariableW (L"PRAAT_AI_PROJECT_DIR", configuredProject, Preferences_STRING_BUFFER_SIZE);
			if (projectLength > 0 && projectLength < Preferences_STRING_BUFFER_SIZE)
				project = std::filesystem::path (configuredProject);
			const auto script = project / "start_api_settings.py";
			if (! std::filesystem::is_regular_file (script))
				Melder_throw (U"没有找到模型设置启动程序，请确认 ai 目录完整。");
			autostring8 python8 = Melder_32to8 (praat_python_getExecutablePath ());
			auto python = std::filesystem::u8path (python8 ? python8. get() : "python");
			wchar_t configuredPython [Preferences_STRING_BUFFER_SIZE] { };
			const DWORD pythonLength = GetEnvironmentVariableW (L"PRAAT_PYTHON_EXECUTABLE", configuredPython, Preferences_STRING_BUFFER_SIZE);
			if (pythonLength > 0 && pythonLength < Preferences_STRING_BUFFER_SIZE)
				python = std::filesystem::path (configuredPython);
			const std::vector<std::pair<std::string, std::string>> values {
				{ "PRAAT_AI_PRAAT_PID", std::to_string (praatProcessId ()) },
				{ "PRAAT_AI_PRAAT_EXECUTABLE", praatExecutablePath (). u8string() }
			};
			std::vector<std::pair<std::string, std::optional<std::string>>> previous;
			for (const auto &value : values) {
				autostring32 name = Melder_8to32_e (value. first. c_str());
				conststring32 old = Melder_getenv (name. get());
				autostring8 old8 = Melder_32to8 (old ? old : U"");
				previous.push_back ({ value. first, old ? std::optional<std::string> (old8. get()) : std::nullopt });
				setEnvironmentUtf8 (value. first. c_str(), value. second);
			}
			std::wstring command = L"\"" + python. wstring() + L"\" \"" + script. wstring() + L"\"";
			STARTUPINFOW startup { }; startup.cb = sizeof (startup);
			PROCESS_INFORMATION process { };
			const BOOL launched = CreateProcessW (nullptr, command. data(), nullptr, nullptr, FALSE,
				CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP, nullptr, project. c_str(), & startup, & process);
			for (const auto &value : previous) {
				if (value. second) setEnvironmentUtf8 (value. first. c_str(), *value. second);
				else clearEnvironmentUtf8 (value. first. c_str());
			}
			if (! launched)
				Melder_throw (U"无法打开模型设置，请检查路径配置中的 Python。");
			CloseHandle (process.hThread); CloseHandle (process.hProcess);
		#else
			runDetachedLauncher (projectDirectoryPath() / "start_api_settings.py");
		#endif
	}

}

void PraatAiControl_initPreferences () {
	Preferences_addString (U"AI.projectDirectory", theAiProjectDirectory, U"ai");
	Preferences_addString (U"AI.alignmentMode", theAiAlignmentMode, U"auto");
	MelderString_copy (& statusFrontendModel, defaultFrontendModel);
	MelderString_copy (& statusFrontendStatus, U"未配置");
	conststring32 configuredDirectory = Melder_getenv (U"PRAAT_AI_PROJECT_DIR");
	if (configuredDirectory && configuredDirectory [0])
		str32cpy (theAiProjectDirectory, configuredDirectory);
}

conststring32 PraatAiControl_getAlignmentMode () {
	return theAiAlignmentMode [0] ? theAiAlignmentMode : U"auto";
}

void PraatAiControl_setAlignmentMode (conststring32 mode) {
	str32cpy (theAiAlignmentMode, mode && mode [0] ? mode : U"auto");
	runControlCommand (U"set-alignment-mode", theAiAlignmentMode);
	PraatAiControl_refreshStatus();
}

bool PraatAiControl_refreshStatus () {
	PraatAiControl_applyPathSettings ();
	std::filesystem::path menuStatusPath = statusPath ();
	#if defined (_WIN32)
		// Sample in a hidden child; never block the editor or export its selected audio.
		static HANDLE sampler = nullptr;
		static ULONGLONG nextSample = 0;
		const auto project = projectDirectoryPath ();
		menuStatusPath = project / "runtime" / ("frontend-menu-" + std::to_string (GetCurrentProcessId ()) + ".json");
		if (sampler && WaitForSingleObject (sampler, 0) == WAIT_OBJECT_0) {
			CloseHandle (sampler);
			sampler = nullptr;
		}
		if (! sampler && GetTickCount64 () >= nextSample) {
			autostring8 python8 = Melder_32to8 (praat_python_getExecutablePath ());
			const auto python = std::filesystem::u8path (python8 ? python8. get() : "python");
			std::wstring command = L"\"" + python.wstring () + L"\" \"" +
				(project / "run_frontend_status.py").wstring () + L"\" --output \"" + menuStatusPath.wstring () + L"\"";
			STARTUPINFOW startup { }; startup.cb = sizeof (startup);
			PROCESS_INFORMATION process { };
			if (CreateProcessW (nullptr, command.data (), nullptr, nullptr, FALSE, CREATE_NO_WINDOW,
				nullptr, project.c_str (), & startup, & process)) {
				sampler = process.hProcess;
				CloseHandle (process.hThread);
			} else {
				const DWORD launchError = GetLastError ();
				std::ofstream diagnostic (menuStatusPath.parent_path () / "frontend-menu-launch-error.txt");
				diagnostic << "CreateProcess error " << launchError << "\n";
			}
			nextSample = GetTickCount64 () + 1800;
		}
	#endif
	const auto text = readTextFile (menuStatusPath);
	if (! text)
		return false;
	// picojson decodes escaped quotes and Unicode preset labels correctly.
	picojson::value snapshot;
	if (! picojson::parse (snapshot, *text).empty () || ! snapshot.is<picojson::object> ())
		return false;
	const auto stringField = [&snapshot] (const char *key, const char *fallback) {
		const auto &value = snapshot.get (key);
		return value.is<std::string> () && ! value.get<std::string> ().empty () ? value.get<std::string> () : std::string (fallback);
	};
	statusSuccess = snapshot.get ("success").is<bool> () && snapshot.get ("success").get<bool> ();
	const std::string model = stringField ("frontend_model", "未配置");
	const std::string status = stringField ("frontend_status", "未配置");
	const auto warning = stringField ("vram_warning", "unknown");
	statusVramWarning = warning == "red" ? 1 : warning == "yellow" ? 2 : warning == "green" ? 3 : 0;
	// Mode is authoritative, even if a previous GPU sample still has a colour.
	statusLocalFrontend = snapshot.get ("frontend_local").is<bool> () && snapshot.get ("frontend_local").get<bool> ();
	if (! statusLocalFrontend)
		statusVramWarning = 0;
	autostring32 model32 = Melder_8to32_e (model. c_str());
	autostring32 status32 = Melder_8to32_e (status. c_str());
	MelderString_copy (& statusFrontendModel, model32 ? model32. get() : U"");
	MelderString_copy (& statusFrontendStatus, status32 ? status32. get() : U"未配置");
	return statusSuccess;
}

conststring32 PraatAiControl_getFrontendModel () {
	return statusFrontendModel. string ? statusFrontendModel. string : defaultFrontendModel;
}

conststring32 PraatAiControl_getFrontendStatus () {
	return statusFrontendStatus. string ? statusFrontendStatus. string : U"未配置";
}

int PraatAiControl_getVramWarning () {
	return statusVramWarning;
}

bool PraatAiControl_isLocalFrontend () {
	return statusLocalFrontend;
}

void PraatAiControl_chooseFrontendModel () {
	autoStringSet files = GuiFileSelect_getInfileNames (
		nullptr,
		U"Select a GGUF model file",
		false
	);
	if (files -> size > 0)
		setModelPath (files -> at [1] -> string. get());
}

void PraatAiControl_configurePaths () {
	#if defined (_WIN32)
		if (pathSettingsProcess) {
			if (WaitForSingleObject (pathSettingsProcess, 0) == WAIT_TIMEOUT) {
				EnumWindows (foregroundPathSettings, static_cast <LPARAM> (pathSettingsPid));
				return;
			}
			// The timer owns the old process until it has consumed its result.
			applyPathSettingsResult ();
			CloseHandle (pathSettingsProcess);
			pathSettingsProcess = nullptr;
			// Its pending timer will poll the new process, so do not schedule a second one.
		}
		const auto executable = praatExecutablePath(). parent_path() / "AIPraat-paths.exe";
		if (! std::filesystem::is_regular_file (executable))
			Melder_throw (U"没有找到 AIPraat-paths.exe，请将路径配置程序与 Praat.exe 放在同一目录。");
		const auto project = projectDirectoryPath ();
		const auto runtime = project / "runtime";
		std::filesystem::create_directories (runtime);
		pathSettingsResult = runtime / ("path-settings-" + std::to_string (praatProcessId ()) + ".txt");
		std::error_code error;
		std::filesystem::remove (pathSettingsResult, error);
		autostring8 python = Melder_32to8 (praat_python_getExecutablePath ());
		autostring8 profile = Melder_32to8 (MelderFolder_peekPath (Melder_preferencesFolder7 ()));
		const std::vector<std::pair<std::string, std::string>> values {
			{ "PRAAT_AI_PATHS_AI_DIR", project. u8string() },
			{ "PRAAT_AI_PATHS_PROFILE_DIR", profile ? profile. get() : "" },
			{ "PRAAT_AI_PATHS_PYTHON", python ? python. get() : "" },
			{ "PRAAT_AI_PATHS_RESULT", pathSettingsResult. u8string() },
			{ "PRAAT_AI_PARENT_PID", std::to_string (praatProcessId ()) }
		};
		std::vector<std::pair<std::string, std::optional<std::string>>> previous;
		for (const auto &value : values) {
			autostring32 name = Melder_8to32_e (value. first. c_str());
			conststring32 old = Melder_getenv (name. get());
			autostring8 old8 = Melder_32to8 (old ? old : U"");
			previous.push_back ({ value. first, old ? std::optional<std::string> (old8. get()) : std::nullopt });
			setEnvironmentUtf8 (value. first. c_str(), value. second);
		}
		std::wstring command = L"\"" + executable. wstring() + L"\"";
		STARTUPINFOW startup { };
		startup.cb = sizeof (startup);
		PROCESS_INFORMATION process { };
		const BOOL launched = CreateProcessW (
			executable. c_str(), command. data(), nullptr, nullptr, FALSE,
			0, nullptr, executable. parent_path(). c_str(), & startup, & process
		);
		for (const auto &value : previous) {
			if (value. second)
				setEnvironmentUtf8 (value. first. c_str(), *value. second);
			else
				clearEnvironmentUtf8 (value. first. c_str());
		}
		if (! launched)
			Melder_throw (U"无法打开路径配置窗口。");
		CloseHandle (process.hThread);
		pathSettingsProcess = process.hProcess;
		pathSettingsPid = process.dwProcessId;
		if (! pathSettingsTimer)
			pathSettingsTimer = SetTimer (nullptr, 0, 250, pathSettingsTimerCallback);
		if (! pathSettingsTimer)
			Melder_throw (U"无法启动路径配置的同步定时器。");
	#else
		Melder_throw (U"此路径配置窗口目前仅支持 Windows。");
	#endif
}

void PraatAiControl_applyPathSettings () {
	#if defined (_WIN32)
		applyPathSettingsResult ();
	#endif
}

static void launchIntegrationWindow (const char *scriptName) {
	#if defined (_WIN32)
		// Configuration windows do not need the Python runner's selected-audio export.
		// That export can show a clipping warning before the window even launches.
		const auto project = projectDirectoryPath ();
		const auto script = project / scriptName;
		if (! std::filesystem::is_regular_file (script))
			Melder_throw (U"没有找到连接管理程序，请确认 ai 目录完整。");
		autostring8 python8 = Melder_32to8 (praat_python_getExecutablePath ());
		const auto python = std::filesystem::u8path (python8 ? python8. get() : "python");
		const std::vector<std::pair<std::string, std::string>> values {
			{ "PRAAT_AI_PRAAT_PID", std::to_string (praatProcessId ()) },
			{ "PRAAT_AI_PRAAT_EXECUTABLE", praatExecutablePath (). u8string() }
		};
		std::vector<std::pair<std::string, std::optional<std::string>>> previous;
		for (const auto &value : values) {
			autostring32 name = Melder_8to32_e (value. first. c_str());
			conststring32 old = Melder_getenv (name. get());
			autostring8 old8 = Melder_32to8 (old ? old : U"");
			previous.push_back ({ value. first, old ? std::optional<std::string> (old8. get()) : std::nullopt });
			setEnvironmentUtf8 (value. first. c_str(), value. second);
		}
		std::wstring command = L"\"" + python. wstring() + L"\" \"" + script. wstring() + L"\"";
		STARTUPINFOW startup { };
		startup.cb = sizeof (startup);
		PROCESS_INFORMATION process { };
		const BOOL launched = CreateProcessW (
			nullptr, command. data(), nullptr, nullptr, FALSE,
			CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP, nullptr, project. c_str(), & startup, & process
		);
		for (const auto &value : previous) {
			if (value. second)
				setEnvironmentUtf8 (value. first. c_str(), *value. second);
			else
				clearEnvironmentUtf8 (value. first. c_str());
		}
		if (! launched)
			Melder_throw (U"无法打开连接管理窗口，请检查路径配置中的 Python。");
		CloseHandle (process.hThread);
		CloseHandle (process.hProcess);
	#else
		runDetachedLauncher (projectDirectoryPath() / (std::string (scriptName) == "run_speech_dictionaries.py" ? "start_speech_dictionaries.py" : "start_anki_integration.py"));
	#endif
}

void PraatAiControl_manageSpeechDictionaries () {
	launchIntegrationWindow ("run_speech_dictionaries.py");
}
void PraatAiControl_openAnkiIntegration () {
	launchIntegrationWindow ("run_anki_integration.py");
}

void PraatAiControl_configureApi () {
	launchModelSettings ();
	PraatAiControl_refreshStatus ();
}

void PraatAiControl_startFrontend () {
	runControlCommand (U"start");
	// Preparation succeeds before the detached desktop is actually connected.
	const auto launchStatus = readTextFile (statusPath ());
	if (launchStatus && jsonBoolField (*launchStatus, "success", false))
		launchAiChatWindow ();
	PraatAiControl_refreshStatus ();
}

void PraatAiControl_stopFrontend () {
	runControlCommand (U"stop");
	PraatAiControl_refreshStatus();
}

void PraatAiControl_runAnalysis () {
	if (! theCurrentPraatObjects || theCurrentPraatObjects -> totalSelection < 2)
		Melder_throw (U"AI 纠音需要至少两个已选中的 Sound 对象。");
	const std::string script8 = (projectDirectoryPath() / "run_ai_tutor.py"). u8string();
	const std::string directory8 = projectDirectoryPath(). u8string();
	autostring32 script32 = Melder_8to32_e (script8. c_str());
	autostring32 directory32 = Melder_8to32_e (directory8. c_str());
	praat_runPythonScriptFile (
		script32 ? script32. get() : U"",
		directory32 ? directory32. get() : nullptr
	);
}

void PraatAiControl_refreshChatContext (bool force) {
	if (Melder_batch)
		return;   // 批处理里没有对话窗口
	writeChatContext (force);   // 默认内容没变化时不重复写盘
}

void PraatAiControl_reportChatScriptFailure (conststring32 message) {
	/*
		app 发来的脚本报错时走这里（sys/praat.cpp 的 cb_userMessage）。

		为什么不直接 Melder_flushError：Windows 上那会开一个
		MessageBox (MB_OK | MB_TOPMOST)，它自带消息循环。用户不点掉它，
		对话窗口就看不到「这一条为什么失败」——只会一条条等到 25 秒超时，
		而且错误原文（英文）还留在那个框里，前端读不到（guide.md §8.5、§8.9）。

		这里改成：错误写进对话窗口读的结果文件（一行，前端直接显示），
		再补一句完成标记，让前端立刻结束等待而不是干等超时。
	*/
	const std::filesystem::path runtimeDirectory = projectDirectoryPath() / "runtime";
	std::error_code error;
	std::filesystem::create_directories (runtimeDirectory, error);
	autostring8 message8 = Melder_32to8 (message ? message : U"");
	const std::string raw = message8 ? message8. get() : "";
	/*
		结果文件一行一条结果，所以把多行错误压成一行：去掉空行、每行首尾空白，
		用 " | " 连起来（Praat 的错误里通常有「命令原文 / 第几行 / 脚本名」三段）。
	*/
	std::string text = "脚本没跑完（Praat 报错，后面的消息不会被它挡住）：";
	std::istringstream lines (raw);
	std::string line;
	bool first = true;
	while (std::getline (lines, line)) {
		const size_t begin = line. find_first_not_of (" \t\r");
		if (begin == std::string::npos)
			continue;
		const size_t end = line. find_last_not_of (" \t\r");
		if (! first)
			text += " | ";
		text += line. substr (begin, end - begin + 1);
		first = false;
	}
	if (first)   // 一个字符都没有（理论上不会）
		text += "（Praat 没有给出错误文字）";
	{
		std::ofstream result (runtimeDirectory / "chat_result.tsv", std::ios::binary | std::ios::app);
		if (result. is_open()) {
			result << text << "\n";
		}
	}
	{
		/*
			另写一份原始错误给前端读：前端看到这个文件就知道「这一条是失败的」，
			而不是把错误行当成正常结果（chat.py 的 _read_failure）。
		*/
		std::ofstream failure (runtimeDirectory / "chat_failure.txt", std::ios::binary | std::ios::trunc);
		if (failure. is_open()) {
			failure << raw << "\n";
		}
	}
	{
		std::ofstream state (runtimeDirectory / "chat_state.txt", std::ios::binary | std::ios::app);
		if (state. is_open())
			state << "done\n";
	}
}

void PraatAiControl_completeChatScriptRequest (conststring32 message) {
	// 消息执行前捕获的文件名是请求身份，不能从可能已被下一请求清除的 started 文件推断。
	autostring8 message8 = Melder_32to8 (message ? message : U"");
	const std::string text = message8 ? message8. get() : "";
	const std::string prefix = "chat_command_";
	const size_t begin = text. rfind (prefix);
	if (begin == std::string::npos)
		return;
	const size_t idBegin = begin + prefix. size();
	const size_t end = text. find (".praat", idBegin);
	if (end == std::string::npos || end == idBegin || end - idBegin > 64)
		return;
	const std::string requestId = text. substr (idBegin, end - idBegin);
	if (requestId. find_first_not_of ("0123456789abcdefABCDEF") != std::string::npos)
		return;
	if (! writeChatContext (true))
		return;   // 对象列表未完整发布时不宣告请求完成。
	std::ofstream finished (projectDirectoryPath() / "runtime" / "chat_finished.txt", std::ios::binary | std::ios::app);
	if (finished. is_open())
		finished << "finished " << requestId << "\n";
}

void PraatAiControl_noteEditorSelection (Thing editor, Thing object, double start, double end) {
	if (Melder_batch)
		return;
	theNotedEditor = editor;
	theNotedEditorObject = object;
	theNotedSelectionStart = start;
	theNotedSelectionEnd = end;
	writeChatContext ();   // 内容没变化时不会重复写盘
}

/* End of file PraatAiControl.cpp */
