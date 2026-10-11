#ifndef _praat_python_workspace_h_
#define _praat_python_workspace_h_

#include <filesystem>
#include <memory>
#include <system_error>
#include <vector>

#if defined (_WIN32)

#include <windows.h>
#include <shlobj.h>

class praat_python_ComScope {
public:
	praat_python_ComScope () : result (CoInitializeEx (nullptr, COINIT_APARTMENTTHREADED)) {
		// A different apartment already satisfies the known-folder COM contract.
		if (FAILED (result) && result != RPC_E_CHANGED_MODE)
			throw std::system_error (static_cast <int> (result), std::system_category (), "Cannot initialize Python workspace COM");
	}
	~praat_python_ComScope () {
		if (SUCCEEDED (result))
			CoUninitialize ();
	}
	praat_python_ComScope (const praat_python_ComScope &) = delete;
	praat_python_ComScope & operator= (const praat_python_ComScope &) = delete;
private:
	HRESULT result;
};

inline bool praat_python_hasLowIntegrity () {
	HANDLE token = nullptr;
	if (! OpenProcessToken (GetCurrentProcess (), TOKEN_QUERY, & token))
		throw std::system_error (GetLastError (), std::system_category (), "Cannot query Python workspace integrity");
	std::unique_ptr<void, decltype (& CloseHandle)> owner (token, & CloseHandle);
	DWORD size = 0;
	GetTokenInformation (token, TokenIntegrityLevel, nullptr, 0, & size);
	if (! size)
		throw std::system_error (GetLastError (), std::system_category (), "Cannot query Python workspace integrity");
	std::vector<BYTE> bytes (size);
	if (! GetTokenInformation (token, TokenIntegrityLevel, bytes. data (), size, & size))
		throw std::system_error (GetLastError (), std::system_category (), "Cannot read Python workspace integrity");
	const auto *label = reinterpret_cast <const TOKEN_MANDATORY_LABEL *> (bytes. data ());
	if (! IsValidSid (label -> Label. Sid) || ! *GetSidSubAuthorityCount (label -> Label. Sid))
		throw std::system_error (ERROR_INVALID_SID, std::system_category (), "Invalid Python workspace integrity");
	const DWORD level = *GetSidSubAuthority (
		label -> Label. Sid, *GetSidSubAuthorityCount (label -> Label. Sid) - 1
	);
	return level < SECURITY_MANDATORY_MEDIUM_RID;
}

#endif

inline std::filesystem::path praat_python_workspaceRoot () {
	#if defined (_WIN32)
		if (praat_python_hasLowIntegrity ()) {
			// Low-integrity processes cannot write the ordinary medium-integrity
			// TEMP directory. Use the Windows-designated writable profile folder.
			praat_python_ComScope com;
			PWSTR folder = nullptr;
			const HRESULT result = SHGetKnownFolderPath (FOLDERID_LocalAppDataLow, KF_FLAG_CREATE, nullptr, & folder);
			std::unique_ptr<wchar_t, decltype (& CoTaskMemFree)> owner (folder, & CoTaskMemFree);
			if (FAILED (result) || ! folder)
				throw std::system_error (static_cast <int> (result), std::system_category (), "Cannot resolve LocalLow Python workspace");
			return std::filesystem::path (folder) / "Praat" / "Python";
		}
	#endif
	return std::filesystem::temp_directory_path ();
}

#endif
