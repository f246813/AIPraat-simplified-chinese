/* Sound_files.cpp
 *
 * Copyright (C) 1992-2018,2020,2022-2024 Paul Boersma & David Weenink
 *
 * This code is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 2 of the License, or (at
 * your option) any later version.
 *
 * This code is distributed in the hope that it will be useful, but
 * WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this work. If not, see <http://www.gnu.org/licenses/>.
 */

/*
 * pb 2002/07/16 GPL
 * pb 2003/09/12 MelderFile_checkSoundFile
 * pb 2004/10/17 test for NULL file pointer when closing in Sound_read(2)FromSoundFile
 * pb 2005/06/17 Mac headers
 * pb 2006/01/05 movies for Mac
 * pb 2006/05/29 QuickTime inclusion made optional
 * pb 2006/10/28 erased MacOS 9 stuff
 * pb 2006/12/30 stereo
 * pb 2006/01/01 more stereo
 * pb 2007/01/28 removed a warning
 * pb 2007/01/28 made readFromMovieFile compatible with stereo
 * pb 2007/03/17 resistant against conflicting declarations of Collection
 * pb 2007/05/08 removed warning about stereo sounds
 * pb 2007/10/05 removed FSSpec
 * pb 2007/10/05 made Sound_readFromMacSoundFile compatible with sampling frequencies between 32768 and 65535 Hz
 * pb 2008/01/19 double
 * pb 2009/09/21 made stereo movies readable
 * pb 2010/12/27 support for multiple channels (i.e. more than two)
 * pb 2011/06/07 C++
 * pb 2012/04/29 removed Sound Manager stuff
 * pb 2012/04/29 removed QuickTime
 */

/*
static void Sound_ulawDecode (Sound me) {
	double mu = 100, lnu1 = log (1 + mu);
	for (integer i = 1; i <= my nx; i ++) {
		double zabs = (exp (fabs (my z [1] [i]) * lnu1) - 1.0) / mu;
		my z [1] [i] = my z [1] [i] < 0 ? -zabs : zabs;
	} 
}
static void Sound_alawDecode (Sound me) {
	double a = 87.6, lna1 = 1.0 + log (a);
	for (integer i = 1; i <= my nx; i ++) {
		double zabs = fabs (my z [1] [i]);
		if (zabs <= 1.0 / lna1) {
			my z [1] [i] *= lna1 / a;
		} else {
			double t = exp (lna1 * zabs - 1.0) / a;
			my z [1] [i] = my z [1] [i] > 0 ? t : - t;
		}
	}
}
*/

#include <time.h>
#include "Sound.h"
#include "../dwtools/Sound_extensions.h"
#include <filesystem>
#include <chrono>
#include <atomic>
#if defined (_WIN32)
#include "Sound_m4a_windows.h"
#endif

static autostring32 (*ffmpegPathProvider) () = nullptr;
void Sound_setFfmpegPathProvider (autostring32 (*provider) ()) {
	ffmpegPathProvider = provider;
}

bool Sound_isAudioFileName (conststring32 name) {
	for (conststring32 extension : { U".wav", U".aiff", U".aif", U".au", U".snd", U".nist", U".flac", U".mp3", U".m4a", U".m4b", U".aac", U".ogg", U".oga", U".opus", U".wma" }) {
		const integer length = Melder_length (name), suffixLength = Melder_length (extension);
		if (length >= suffixLength && str32equ_caseInsensitive (name + length - suffixLength, extension))
			return true;
	}
	return false;
}

static autoSound Sound_readNativeAudio (MelderFile file) {
	try {
		// The full Praat application has Vorbis/Opus readers that Parselmouth lacks.
		{
			autofile input = Melder_fopen (file, "rb");
			char header [36] { };
			const size_t count = fread (header, 1, sizeof header, input);
			if (count >= 36 && strnequ (header, "OggS", 4))
				return strnequ (header + 28, "OpusHead", 8) ? Sound_readFromOggOpusFile (file) : Sound_readFromOggVorbisFile (file);
		}
		#if defined (_WIN32)
			if (Melder_endsWith_caseAware (MelderFile_name (file), U".m4a"))
				return Sound_readM4aWindows (file);
		#endif
		autoMelderFile mfile = MelderFile_open (file);
		int encoding;
		double sampleRate;
		integer startOfData, numberOfSamples, numberOfChannels;
		int fileType = MelderFile_checkSoundFile (file, & numberOfChannels, & encoding, & sampleRate, & startOfData, & numberOfSamples);
		if (fileType == 0)
			Melder_throw (U"Not an audio file.");
		try {
			MelderFile_seek (file, startOfData, SEEK_SET);   // start from beginning of Data Chunk
		} catch (MelderError) {
			Melder_throw (U"No data in audio file.");
		}
		if (numberOfSamples < 1)
			Melder_throw (U"Audio file contains 0 samples.");
		autoSound me = Sound_createSimple (numberOfChannels, numberOfSamples / sampleRate, sampleRate);
		Melder_assert (my z.ncol == numberOfSamples);
		if (encoding == Melder_SHORTEN)
			Melder_throw (U"Cannot unshorten. Write to paul.boersma@uva.nl for more information.");
		Melder_readAudioToFloat (file, encoding, my z.get());
		mfile.close ();
		return me;
	} catch (MelderError) {
		Melder_throw (U"Sound not read from sound file ", file, U".");
	}
}

autoSound Sound_readFromSoundFile (MelderFile file, bool *converted) {
	if (converted) *converted = false;
	try {
		return Sound_readNativeAudio (file);
	} catch (MelderError) {
		autostring32 nativeError = Melder_dup (Melder_getError ());
		Melder_clearError ();
		// Decode only after a completed, failed native read. The retry reads WAV
		// natively, so a bad WAV cannot recurse into another conversion.
		std::filesystem::path directory;
		try {
			autostring32 executable = ffmpegPathProvider ? ffmpegPathProvider () : Melder_dup (U"ffmpeg");
			static std::atomic<unsigned long> sequence { 0 };
			for (unsigned int attempt = 0; attempt < 100; attempt ++) {
				directory = std::filesystem::temp_directory_path () / ("aipraat-audio-" +
					std::to_string (std::chrono::high_resolution_clock::now ().time_since_epoch ().count ()) + "-" + std::to_string (++ sequence));
				if (std::filesystem::create_directory (directory)) break;
				directory.clear ();
			}
			if (directory.empty ()) Melder_throw (U"无法创建音频解码临时目录。");
			const auto wav = directory / "decoded.wav";
			autostring32 wav32 = Melder_8to32_e (wav.u8string ().c_str ());
			autoSTRVEC arguments { U"-nostdin", U"-v", U"error", U"-i", MelderFile_peekPath (file),
				U"-map", U"0:a:0", U"-vn", U"-c:a", U"pcm_s24le", U"-y", wav32.get () };
			Melder_runSubprocess (executable.get (), arguments.size, arguments.peek2 ());
			structMelderFile temporary;
			Melder_pathToFile (wav32.get (), & temporary);
			autoSound sound = Sound_readNativeAudio (& temporary);
			std::error_code cleanupError;
			std::filesystem::remove_all (directory, cleanupError);
			if (converted) *converted = true;
			return sound;
		} catch (MelderError) {
			autostring32 fallbackError = Melder_dup (Melder_getError ());
			Melder_clearError ();
			std::error_code cleanupError;
			if (! directory.empty ()) std::filesystem::remove_all (directory, cleanupError);
			Melder_throw (U"原生音频读取失败：\n", nativeError.get (), U"\n临时 WAV 备用解码失败：\n", fallbackError.get (),
				U"\n请在路径配置中检测或安装 FFmpeg。");
		} catch (const std::exception &error) {
			std::error_code cleanupError;
			if (! directory.empty ()) std::filesystem::remove_all (directory, cleanupError);
			Melder_throw (U"原生音频读取失败：\n", nativeError.get (), U"\n备用解码失败：", Melder_peek8to32_u (error.what ()));
		}
	}
}

autoSound Sound_readFromSesamFile (MelderFile file) {
	try {
		autofile f = Melder_fopen (file, "rb");
		int32 header [1 + 128];
		for (integer i = 1; i <= 128; i ++)
			header [i] = bingeti32LE (f);
		/*
		 * Try SESAM header.
		 */
		double samplingFrequency = header [126];   // converting up (from 32 to 54 bits)
		int32 numberOfSamples = header [127];
		if (samplingFrequency == 0.0 || numberOfSamples == 0) {
			/*
				Try LVS header.
			*/
			samplingFrequency = header [62];
			numberOfSamples = (header [6] << 8) - header [68];
		}
		if (numberOfSamples < 1 || numberOfSamples > 1000000000 || samplingFrequency < 10.0 || samplingFrequency > 100000000.0)
			Melder_throw (U"Not a correct SESAM or LVS file.");
		autoSound me = Sound_createSimple (1, numberOfSamples / samplingFrequency, samplingFrequency);
		for (int32 i = 1; i <= numberOfSamples; i ++) {
			my z [1] [i] = (double) bingeti16LE (f) * (1.0 / 2048);   // 12 bits
		}
		f.close (file);
		return me;
	} catch (MelderError) {
		Melder_throw (U"Sound not read from Sesam file ", file, U".");
	}
}

autoSound Sound_readFromBellLabsFile (MelderFile file) {
	try {
		autofile f = Melder_fopen (file, "rb");

		/*
			Check identity of file: first line is "SIG", second line contains a number.
		*/
		char tag [200];
		if (fread (tag, 1, 16, f) < 16 || ! strnequ (tag, "SIG\n", 4))
			Melder_throw (U"Not a Bell-Labs sound file.");
		char *endOfTag = strchr (tag + 4, '\n');
		if (! endOfTag)
			Melder_throw (U"Second line missing or too long.");
		integer tagLength = (endOfTag - tag) + 1;   // probably 12
		int64 headerLength = atol (tag + 4);
		if (headerLength <= 0)
			Melder_throw (U"Wrong header-length info.");

		/*
			Read data from header.
			Use defaults if necessary.
		*/
		autostring8 lines (headerLength);
		if ((int64) fread (lines.get(), 1, (size_t) headerLength, f) < headerLength)
			Melder_throw (U"Header too short.");
		integer numberOfSamples = 0;
		char *psamples = & lines [-1];
		while (!! (psamples = strstr (psamples + 1, "samples ")))   // take last occurrence
			numberOfSamples = atol (psamples + 8);
		if (numberOfSamples < 1) {
			/* Use file length. */
			fseek (f, 0, SEEK_END);   /* Position file pointer at end of file. */
			numberOfSamples = (ftell (f) - tagLength - headerLength) / 2;
		}
		if (numberOfSamples < 1)
			Melder_throw (U"No samples found.");
		double samplingFrequency = 0.0;
		char *pfrequency = & lines [-1];
		while (!! (pfrequency = strstr (pfrequency + 1, "frequency ")))   // take last occurrence
			samplingFrequency = atof (pfrequency + 10);
		if (samplingFrequency <= 0.0)
			samplingFrequency = 16000.0;

		/*
			Create sound.
		*/
		autoSound me = Sound_createSimple (1, numberOfSamples / samplingFrequency, samplingFrequency);

		/*
			Read samples.
		*/
		fseek (f, tagLength + headerLength, SEEK_SET);
		for (integer i = 1; i <= numberOfSamples; i ++)
			my z [1] [i] = (double) bingeti16 (f) * (1.0 / 32768);   // 16-bits big-endian

		f.close (file);
		return me;
	} catch (MelderError) {
		Melder_throw (U"Sound not read from Bell Labs sound file ", file, U".");
	}
}

static void readError () {
	Melder_throw (U"Error reading bytes from file.");
}

static autoDaata Sound_readFromKayFile (MelderFile file) {
	FILE *f = file -> filePointer;
	uint32 chunkSize = bingetu32LE (f);
	if (chunkSize & 1)
		chunkSize += 1;   // round up to even
	if (chunkSize != 32 && chunkSize != 44)
		Melder_throw (U"Unknown chunk size ", chunkSize, U". Please report to paul.boersma@uva.nl.");
	char data [100];
	if (fread (data, 1, 20, f) < 20)
		readError ();
	const double samplingFrequency = bingetu32LE (f);   // converting up (from 32 to 53 bits)
	const integer numberOfSamples = bingetu32LE (f);
	if (samplingFrequency <= 0 || samplingFrequency > 1e7 || numberOfSamples >= 1000000000)
		Melder_throw (U"Not a correct Kay file.");
	const int16 tmp1 = bingeti16LE (f);
	const int16 tmp2 = bingeti16LE (f);
	integer numberOfChannels = ( tmp1 == -1 || tmp2 == -1 ? 1 : 2 );
	if (chunkSize == 44) {
		const int16 tmp3 = bingeti16LE (f);
		if (tmp3 != -1)
			numberOfChannels ++;
		const int16 tmp4 = bingeti16LE (f);
		if (tmp4 != -1)
			numberOfChannels ++;
		const int16 tmp5 = bingeti16LE (f);
		if (tmp5 != -1)
			numberOfChannels ++;
		const int16 tmp6 = bingeti16LE (f);
		if (tmp6 != -1)
			numberOfChannels ++;
		const int16 tmp7 = bingeti16LE (f);
		if (tmp7 != -1)
			numberOfChannels ++;
		const int16 tmp8 = bingeti16LE (f);
		if (tmp8 != -1)
			numberOfChannels ++;
	}
	/*
		SD chunk.
	*/
	autoSound me = Sound_createSimple (numberOfChannels, numberOfSamples / samplingFrequency, samplingFrequency);
	for (integer ichan = 1; ichan <= numberOfChannels; ichan ++) {
		if (fread (data, 1, 4, f) < 4)
			readError ();
		while (! strnequ (data, "SD", 2)) {
			if (feof ((FILE *) f))
				Melder_throw (U"Missing or unreadable SD chunk. Please report to paul.boersma@uva.nl.");
			chunkSize = bingetu32LE (f);
			if (chunkSize & 1)
				chunkSize += 1;   // round up to even
			Melder_casual (U"Chunk ",
				data [0], U" ", data [1], U" ", data [2], U" ", data [3], U" ", chunkSize);
			fseek (f, chunkSize, SEEK_CUR);
			if (fread (data, 1, 4, f) < 4)
				readError ();
		}
		chunkSize = bingetu32LE (f);
		integer residual = chunkSize - numberOfSamples * 2;
		if (residual == -2 || residual == 2) {
			const integer localNumberOfSamples = numberOfSamples + residual / 2;
			//TRACE
			trace (U"Reading ", localNumberOfSamples, U" samples instead of the promised ", numberOfSamples, U".");
			if (ichan == 1) {
				me = Sound_extractPart (me.get(), 0.0, localNumberOfSamples / samplingFrequency, kSound_windowShape::RECTANGULAR, 1.0, true);
				Melder_assert (my nx == localNumberOfSamples);
			} else {
				Melder_require (my nx == localNumberOfSamples,
						U"Not all channels have the same numbers of samples.");
			}
			for (integer i = 1; i <= localNumberOfSamples; i ++)
				my z [ichan] [i] = (double) bingeti16LE (f) / 32768.0;
		} else if (residual != 0) {
			Melder_throw (U"Incomplete SD chunk: attested size ", chunkSize, U" bytes,"
				U" announced size ", numberOfSamples * 2, U" bytes. Please report to paul.boersma@uva.nl.");
		} else {
			for (integer i = 1; i <= numberOfSamples; i ++)
				my z [ichan] [i] = (double) bingeti16LE (f) / 32768.0;
		}
	}
	//Melder_casual (ftell (f));
	return me;
}
static autoDaata Sounds_readFromKayNasalityFile (MelderFile file) {
	FILE *f = file -> filePointer;
	uint32 chunkSize = bingetu32LE (f);
	if (chunkSize & 1)
		chunkSize += 1;   // round up to even
	if (chunkSize != 100)
		Melder_throw (U"Unknown chunk size ", chunkSize, U". Please report to paul.boersma@uva.nl.");
	char data [100];
	if (fread (data, 1, 20, f) < 20)
		readError ();
	const int16 tmp1 = bingeti16LE (f);
	const int16 tmp2 = bingeti16LE (f);
	integer numberOfChannels = ( tmp1 == -1 || tmp2 == -1 ? 1 : 2 );
	const int16 tmp3 = bingeti16LE (f);
	if (tmp3 != -1)
		numberOfChannels ++;
	const int16 tmp4 = bingeti16LE (f);
	if (tmp4 != -1)
		numberOfChannels ++;
	const int16 tmp5 = bingeti16LE (f);
	if (tmp5 != -1)
		numberOfChannels ++;
	const int16 tmp6 = bingeti16LE (f);
	if (tmp6 != -1)
		numberOfChannels ++;
	const int16 tmp7 = bingeti16LE (f);
	if (tmp7 != -1)
		numberOfChannels ++;
	const int16 tmp8 = bingeti16LE (f);
	if (tmp8 != -1)
		numberOfChannels ++;
	Melder_require (numberOfChannels >= 2,
		U"Expecting at least two channels (nasal and oral) instead of ", numberOfChannels,
		U". Please report to paul.boersma@uva.nl."
	);
	Melder_require (numberOfChannels <= 3,
		U"Expecting at most three channels (nasal, oral and nasalance) instead of ", numberOfChannels,
		U". Please report to paul.boersma@uva.nl."
	);
	double channelSamplingFrequencies [1+8] = { 0.0, undefined, undefined, undefined, undefined, undefined, undefined, undefined, undefined };
	for (int ichan = 1; ichan <= 8; ichan ++)
		channelSamplingFrequencies [ichan] = bingetu32LE (f);   // converting up (from 32 to 53 bits)
	Melder_require (channelSamplingFrequencies [2] == channelSamplingFrequencies [1],
		U"Expecting equal sampling frequencies for channels 1 (nasal) and 2 (oral), "
		"but found differing sampling frequencies (", channelSamplingFrequencies [1],
		U" and ", channelSamplingFrequencies [2], U" Hz, respectively). Please report to paul.boersma@uva.nl."
	);
	for (integer ichan = numberOfChannels + 1; ichan <= 8; ichan ++)
		Melder_require (channelSamplingFrequencies [ichan] == 0.0,
			U"Sampling frequency of non-existent channel ", ichan,
			U" should be 0 but is ", channelSamplingFrequencies [ichan],
			U" Hz. Please report to paul.boersma@uva.nl."
		);
	integer channelNumbersOfSamples [1+8] = { 0, -1, -1, -1, -1, -1, -1, -1, -1 };
	for (integer ichan = 1; ichan <= 8; ichan ++)
		channelNumbersOfSamples [ichan] = bingetu32LE (f);
	Melder_require (channelNumbersOfSamples [2] == channelNumbersOfSamples [1],
		U"Expecting equal numbers of samples in channels 1 (nasal) and 2 (oral), "
		"but found differing numbers of samples (", channelNumbersOfSamples [1],
		U" and ", channelNumbersOfSamples [2], U", respectively). Please report to paul.boersma@uva.nl."
	);
	for (integer ichan = numberOfChannels + 1; ichan <= 8; ichan ++)
		Melder_require (channelNumbersOfSamples [ichan] == 0,
			U"Sample count of non-existent channel ", ichan,
			U" should be 0 but is ", channelNumbersOfSamples [ichan],
			U". Please report to paul.boersma@uva.nl."
		);
	/*
		SD chunk.
	*/
	autoCollection me = Collection_create ();
	for (integer ichan = 1; ichan <= numberOfChannels; ichan ++) {
		if (fread (data, 1, 4, f) < 4)
			readError ();
		while (! strnequ (data, "SD", 2)) {
			if (feof ((FILE *) f))
				Melder_throw (U"Missing or unreadable SD chunk. Please report to paul.boersma@uva.nl.");
			chunkSize = bingetu32LE (f);
			if (chunkSize & 1)
				chunkSize += 1;   // round up to even
			Melder_casual (U"Chunk ",
				data [0], U" ", data [1], U" ", data [2], U" ", data [3], U" ", chunkSize);
			fseek (f, chunkSize, SEEK_CUR);
			if (fread (data, 1, 4, f) < 4)
				readError ();
		}
		chunkSize = bingetu32LE (f);
		#if 0
			integer residual = chunkSize - numberOfSamples * 2;
			if (residual < 0)
				Melder_throw (U"Incomplete SD chunk: attested size ", chunkSize, U" bytes,"
					U" announced size ", numberOfSamples * 2, U" bytes. Please report to paul.boersma@uva.nl.");
		#endif
		autoSound you = Sound_createSimple (1,
			channelNumbersOfSamples [ichan] / channelSamplingFrequencies [ichan],
			channelSamplingFrequencies [ichan]
		);
		if (channelNumbersOfSamples [ichan] == channelNumbersOfSamples [1])
			for (integer i = 1; i <= channelNumbersOfSamples [ichan]; i ++)
				your z [1] [i] = (double) bingeti16LE (f) / 32768.0;
		else
			for (integer i = 1; i <= channelNumbersOfSamples [ichan]; i ++)
				your z [1] [i] = (double) bingeti16LE (f) / 100.0;
		//fseek (f, residual, SEEK_CUR);
		Thing_setName (you.get(), ichan == 1 ? U"_nasal" : ichan == 2 ? U"_oral" : U"_nasalance");
		my addItem_move (you.move());
	}
	//Melder_casual (ftell (f));
	return me;
}

autoDaata Sound_readFromAnyKayFile (MelderFile file) {
	try {
		autofile f = Melder_fopen (file, "rb");
		/*
			Read header of KAY file: 12 bytes.
		*/
		char data [100];
		if (fread (data, 1, 8, f) < 8)
			readError ();
		if (! strnequ (data, "FORMDS16", 8))
			Melder_throw (U"Not a KAY DS-16 file.");
		if (fread (data, 1, 4, f) < 4)   // this contains the size of the DS16 chunk: typically 12 bytes less than the size of the file
			readError ();
		/*
			Read header of HEDR or HDR8 or NHDR chunk.
		*/
		if (fread (data, 1, 4, f) < 4)
			readError ();
		if (! strnequ (data, "HEDR", 4) && ! strnequ (data, "HDR8", 4) && ! strnequ (data, "NHDR", 4))
			Melder_throw (U"Missing HEDR or HDR8 or NHDR chunk. Please report to paul.boersma@uva.nl.");
		/*
			Read remainder of HEDR or HDR8 or NHDR chunk.
		*/
		file -> filePointer = f;
		autoDaata result = ( strnequ (data, "NHDR", 4) ? Sounds_readFromKayNasalityFile (file) : Sound_readFromKayFile (file) );
		f.close (file);
		return result;
	} catch (MelderError) {
		Melder_throw (U"Sound not read from Kay file ", file, U".");
	}
}

autoSound Sound_readFromRawAlawFile (MelderFile file) {
	try {
		double sampleRate = 8000.0;
		autoMelderFile mfile = MelderFile_open (file);
		MelderFile_seek (file, 0, SEEK_END);
		integer numberOfSamples = MelderFile_tell (file);
		MelderFile_rewind (file);
		autoSound me = Sound_createSimple (1, numberOfSamples / sampleRate, sampleRate);
		Melder_assert (my z.ncol == numberOfSamples);
		Melder_readAudioToFloat (file, Melder_ALAW, my z.get());
		mfile.close ();
		return me;
	} catch (MelderError) {
		Melder_throw (U"Sound not read from raw A-law file ", file, U".");
	}
}

void Sound_saveAsAudioFile (constSound me, MelderFile file, int audioFileType, int numberOfBitsPerSamplePoint) {
	try {
		autoMelderFile mfile = MelderFile_create (file);
		MelderFile_writeAudioFileHeader (file, audioFileType, Melder_iround_tieDown (1.0 / my dx), my nx, my ny, numberOfBitsPerSamplePoint);
		MelderFile_writeFloatToAudio (file, my z.get(), Melder_defaultAudioFileEncoding (audioFileType, numberOfBitsPerSamplePoint), true);
		MelderFile_writeAudioFileTrailer (file, audioFileType, Melder_iround (1.0 / my dx), my nx, my ny, numberOfBitsPerSamplePoint);
		mfile.close ();
	} catch (MelderError) {
		Melder_throw (me, U": not written to 16-bit sound file ", file, U".");
	}
}

void Sound_saveAsSesamFile (constSound me, MelderFile file) {
	try {
		autofile f = Melder_fopen (file, "wb");
		integer header [1 + 128];
		for (integer i = 1; i <= 128; i ++)
			header [i] = 0;
		/*
			ILS header.
		*/
		header [6] = ((my nx - 1) >> 8) + 1;   // number of disk blocks
		header [64] = 32149;   // ILS magic
		/*
			LVS header.
		*/
		header [62] = Melder_iround_tieDown (1.0 / my dx);   // sampling frequency, rounded to n Hz
		header [63] = -32000;   // magic: "sampled signal"
		header [66] = INT12_MAX;   // maximum absolute value: 12 bits
		header [67] = 2047;   // LVS magic
		header [68] = my nx % 256;   // number of samples in last block
		header [69] = 1;   // ?
		/*
			Sesam header.
		*/
		header [126] = Melder_iround_tieDown (1.0 / my dx);   // sampling frequency, rounded to n Hz
		header [127] = my nx;   // number of samples
		for (integer i = 1; i <= 128; i ++)
			binputi32LE (header [i], f);
		for (integer i = 1; i <= my nx; i ++)
			binputi16LE (Melder_iround_tieDown (my z [1] [i] * 2048.0), f);
		integer tail = 256 - my nx % 256;
		if (tail == 256)
			tail = 0;
		for (integer i = 1; i <= tail; i ++)
			binputi16LE (0, f);   // pad last block with zeroes
		f.close (file);
	} catch (MelderError) {
		Melder_throw (me, U": not written to Sesam file ", file, U".");
	}
}

void Sound_saveAsKayFile (constSound me, MelderFile file) {
	try {
		Melder_require (my ny <= 8,
			U"Cannot write more than 8 channels into a Kay sound file.");
		constexpr int32 maximumNumberOfSamples32 = (INT32_MAX - 48) / 2;
		Melder_require (my nx <= maximumNumberOfSamples32,
			U"Cannot write more than ", maximumNumberOfSamples32, U" samples into a Kay sound file.");
		const int32 numberOfSamples32 = int (my nx);

		autoMelderFile mfile = MelderFile_create (file);

		/*
			Form Chunk: contains all other chunks.
		*/
		fwrite ("FORMDS16", 1, 8, file -> filePointer);
		binputi32LE (48 + numberOfSamples32 * 2, file -> filePointer);   // size of Form Chunk
		fwrite (my ny > 2 ? "HDR8" : "HEDR", 1, 4, file -> filePointer);
		binputi32LE (my ny > 2 ? 44 : 32, file -> filePointer);

		char date [100];
		time_t today = time (nullptr);
		strcpy (date, ctime (& today));	
		fwrite (date+4, 1, 20, file -> filePointer);   // skip weekday

		binputi32LE (Melder_iround_tieDown (1.0 / my dx), file -> filePointer);   // sampling frequency
		binputi32LE (numberOfSamples32, file -> filePointer);   // number of samples
		int maximumA = 0;
		for (integer i = 1; i <= my nx; i ++) {
			const integer value = Melder_iround_tieDown (my z [1] [i] * 32768.0);
			if (value < - maximumA)
				maximumA = - value;
			if (value > maximumA)
				maximumA = value;
		}
		binputi16LE (maximumA, file -> filePointer);   // absolute maximum window A
		if (my ny == 1) {
			binputi16LE (-1, file -> filePointer);
		} else {
			for (integer ichannel = 2; ichannel <= my ny; ichannel ++) {
				int maximum = 0;
				for (integer i = 1; i <= my nx; i ++) {
					const integer value = Melder_iround_tieDown (my z [ichannel] [i] * 32768.0);
					if (value < - maximum)
						maximum = - value;
					if (value > maximum)
						maximum = value;
				}
				binputi16LE (maximum, file -> filePointer);   // absolute maximum window B
			}
			if (my ny > 2)
				for (integer ichannel = my ny + 1; ichannel <= 8; ichannel ++)
					binputi16LE (-1, file -> filePointer);
		}
		fwrite ("SDA_", 1, 4, file -> filePointer);
		binputi32LE (numberOfSamples32 * 2, file -> filePointer);   // chunk size
		MelderFile_writeFloatToAudio (file,
				my z.horizontalBand (1, 1), Melder_LINEAR_16_LITTLE_ENDIAN, true);
		if (my ny > 1) {
			fwrite ("SD_B", 1, 4, file -> filePointer);
			binputi32LE (numberOfSamples32 * 2, file -> filePointer);   // chunk size
			MelderFile_writeFloatToAudio (file,
					my z.horizontalBand (2, 2), Melder_LINEAR_16_LITTLE_ENDIAN, true);
		}
		for (integer ichannel = 3; ichannel <= my ny; ichannel ++) {
			fwrite (Melder_peek32to8 (Melder_cat (U"SD_", ichannel)), 1, 4, file -> filePointer);
			binputi32LE (numberOfSamples32 * 2, file -> filePointer);   // chunk size
			MelderFile_writeFloatToAudio (file,
					my z.horizontalBand (ichannel, ichannel), Melder_LINEAR_16_LITTLE_ENDIAN, true);
		}
		mfile.close ();
	} catch (MelderError) {
		Melder_throw (me, U": not written to Kay sound file ", file, U".");
	}
}

void Sound_saveAsRawSoundFile (constSound me, MelderFile file, int encoding) {
	try {
		autoMelderFile mfile = MelderFile_create (file);
		MelderFile_writeFloatToAudio (file, my z.get(), encoding, true);
		mfile.close ();
	} catch (MelderError) {
		Melder_throw (me, U": not written to raw sound file ", file, U".");
	}
}

/* End of file Sound_files.cpp */
