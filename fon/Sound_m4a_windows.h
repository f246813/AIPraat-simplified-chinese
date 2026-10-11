/* Windows system decoder for M4A. Decoded samples go directly into Sound;
   no transcoder process, intermediate WAV, resampling, or channel downmix. */
#include <windows.h>
#include <mfapi.h>
#include <mfidl.h>
#include <mfreadwrite.h>
#include <vector>
#include <cstring>

namespace {
template <typename T> struct M4aCom {
	T *p = nullptr;
	~M4aCom () { if (p) p -> Release (); }
	T *operator -> () const { return p; }
	T **out () { return & p; }
};
struct M4aRuntime {
	HMODULE platform = nullptr, readwrite = nullptr;
	bool comInitialized = false, started = false;
	decltype (& MFShutdown) shutdown = nullptr;
	~M4aRuntime () {
		if (started) shutdown ();
		if (readwrite) FreeLibrary (readwrite);
		if (platform) FreeLibrary (platform);
		if (comInitialized) CoUninitialize ();
	}
};
void m4aCheck (HRESULT result, conststring32 operation) {
	if (FAILED (result))
		Melder_throw (U"M4A 系统解码失败：", operation, U" (HRESULT ", (integer) result, U")。可通过 Anki 连接中的临时 WAV 备用路径打开。");
}
autoSound Sound_readM4aWindows (MelderFile file) {
	M4aRuntime runtime;
	const HRESULT com = CoInitializeEx (nullptr, COINIT_APARTMENTTHREADED);
	if (com != RPC_E_CHANGED_MODE) m4aCheck (com, U"初始化 COM");
	runtime.comInitialized = SUCCEEDED (com);
	// Load at the time of use: Windows N without Media Foundation can still
	// start Praat and use the explicitly retained external-decoder fallback.
	runtime.platform = LoadLibraryExW (L"mfplat.dll", nullptr, LOAD_LIBRARY_SEARCH_SYSTEM32);
	runtime.readwrite = LoadLibraryExW (L"mfreadwrite.dll", nullptr, LOAD_LIBRARY_SEARCH_SYSTEM32);
	if (! runtime.platform || ! runtime.readwrite)
		Melder_throw (U"当前 Windows 未提供 Media Foundation；请使用临时 WAV 备用解码。");
	auto startup = reinterpret_cast <decltype (& MFStartup)> (GetProcAddress (runtime.platform, "MFStartup"));
	runtime.shutdown = reinterpret_cast <decltype (& MFShutdown)> (GetProcAddress (runtime.platform, "MFShutdown"));
	auto createType = reinterpret_cast <decltype (& MFCreateMediaType)> (GetProcAddress (runtime.platform, "MFCreateMediaType"));
	auto createReader = reinterpret_cast <decltype (& MFCreateSourceReaderFromURL)> (GetProcAddress (runtime.readwrite, "MFCreateSourceReaderFromURL"));
	if (! startup || ! runtime.shutdown || ! createType || ! createReader)
		Melder_throw (U"Windows 系统音频解码接口不可用。");
	m4aCheck (startup (MF_VERSION, MFSTARTUP_FULL), U"启动解码器");
	runtime.started = true;
	M4aCom <IMFSourceReader> reader;
	autostringW path = Melder_32toW (MelderFile_peekPath (file));
	m4aCheck (createReader (path.get(), nullptr, reader.out()), U"读取 M4A");
	m4aCheck (reader -> SetStreamSelection (MF_SOURCE_READER_ALL_STREAMS, FALSE), U"选择流");
	m4aCheck (reader -> SetStreamSelection (MF_SOURCE_READER_FIRST_AUDIO_STREAM, TRUE), U"选择音频流");
	M4aCom <IMFMediaType> type;
	m4aCheck (createType (type.out()), U"创建音频格式");
	m4aCheck (type -> SetGUID (MF_MT_MAJOR_TYPE, MFMediaType_Audio), U"设置音频类型");
	m4aCheck (type -> SetGUID (MF_MT_SUBTYPE, MFAudioFormat_Float), U"设置浮点 PCM");
	m4aCheck (reader -> SetCurrentMediaType (MF_SOURCE_READER_FIRST_AUDIO_STREAM, nullptr, type.p), U"解码为浮点 PCM");
	M4aCom <IMFMediaType> decoded;
	m4aCheck (reader -> GetCurrentMediaType (MF_SOURCE_READER_FIRST_AUDIO_STREAM, decoded.out()), U"获取解码格式");
	UINT32 rate = 0, channels = 0, bits = 0;
	m4aCheck (decoded -> GetUINT32 (MF_MT_AUDIO_SAMPLES_PER_SECOND, & rate), U"获取采样率");
	m4aCheck (decoded -> GetUINT32 (MF_MT_AUDIO_NUM_CHANNELS, & channels), U"获取声道数");
	m4aCheck (decoded -> GetUINT32 (MF_MT_AUDIO_BITS_PER_SAMPLE, & bits), U"获取位深");
	if (! rate || ! channels || bits != 32)
		Melder_throw (U"M4A 解码器返回了不支持的 PCM 格式。");
	std::vector <float> samples;
	for (;;) {
		M4aCom <IMFSample> sample;
		DWORD flags = 0;
		m4aCheck (reader -> ReadSample (MF_SOURCE_READER_FIRST_AUDIO_STREAM, 0, nullptr, & flags, nullptr, sample.out()), U"读取音频样本");
		if (flags & MF_SOURCE_READERF_ERROR)
			Melder_throw (U"M4A 解码器报告了音频流错误。");
		if (flags & MF_SOURCE_READERF_CURRENTMEDIATYPECHANGED)
			Melder_throw (U"M4A 音频在文件中途改变了采样格式，请使用备用解码。");
		if (sample.p) {
			M4aCom <IMFMediaBuffer> buffer;
			m4aCheck (sample -> ConvertToContiguousBuffer (buffer.out()), U"获取音频缓冲区");
			BYTE *data = nullptr;
			DWORD size = 0;
			m4aCheck (buffer -> Lock (& data, nullptr, & size), U"锁定缓冲区");
			if (size % (channels * sizeof (float))) {
				buffer -> Unlock ();
				Melder_throw (U"M4A 音频帧不完整。");
			}
			try {
				const size_t oldSize = samples.size();
				samples.resize (oldSize + size / sizeof (float));
				std::memcpy (samples.data() + oldSize, data, size);
			} catch (...) { buffer -> Unlock (); throw; }
			m4aCheck (buffer -> Unlock (), U"释放缓冲区");
		}
		if (flags & MF_SOURCE_READERF_ENDOFSTREAM) break;
	}
	const integer frames = (integer) (samples.size() / channels);
	if (! frames) Melder_throw (U"M4A 音频没有可读取的样本。");
	autoSound sound = Sound_createSimple (channels, (double) frames / rate, rate);
	for (integer frame = 1; frame <= frames; frame ++)
		for (integer channel = 1; channel <= channels; channel ++)
			sound -> z [channel] [frame] = samples [(frame - 1) * channels + channel - 1];
	return sound;
}
}
