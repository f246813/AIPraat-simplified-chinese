/* Standalone regression driver linked to the real Praat runtime libraries. */
#include "praat.h"
#include "praat_python.h"
#include "PraatAiControl.h"
#include <chrono>
#include <iostream>

int main (int argc, char **argv) {
	if (argc < 4) return 2;
	Melder_batch = true;
	praat_python_initPreferences ();
	praat_python_setExecutablePath (Melder_peek8to32_u (argv [1]));
	autoMelderString capturedInfo;
	autoMelderDivertInfo divert (& capturedInfo);
	const auto started = std::chrono::steady_clock::now ();
	int result = 0;
	try {
		if (std::string (argv [2]) == "start") {
			PraatAiControl_initPreferences ();
			PraatAiControl_startFrontend ();
		} else {
			autostring32 script = Melder_8to32_e (argv [2]);
			autostring32 directory = Melder_8to32_e (argv [3]);
			const unsigned int timeout = argc > 4 ? std::stoul (argv [4]) : 200;
			praat_runPythonScriptFile (script.get (), directory.get (), timeout);
		}
	} catch (MelderError) {
		std::cerr << Melder_peek32to8 (Melder_getError ()) << "\n";
		Melder_clearError ();
		result = 1;
	}
	std::cout << Melder_peek32to8 (Melder_getInfo ());
	std::cout << "elapsed_ms=" << std::chrono::duration_cast<std::chrono::milliseconds> (
		std::chrono::steady_clock::now () - started).count () << "\n";
	return result;
}
