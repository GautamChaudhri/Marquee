export const KNOWN_ENCODERS = [
	'hevc_nvenc',
	'h264_nvenc',
	'hevc_qsv',
	'h264_qsv',
	'hevc_vaapi',
	'h264_vaapi',
	'libx265',
	'libx264'
];

export const NVENC_PRESETS = ['p1', 'p2', 'p3', 'p4', 'p5', 'p6', 'p7'];

export const CPU_PRESETS = [
	'ultrafast',
	'superfast',
	'veryfast',
	'faster',
	'fast',
	'medium',
	'slow',
	'slower',
	'veryslow'
];

export const ENCODER_LABELS: Record<string, string> = {
	hevc_nvenc: 'NVIDIA NVENC (HEVC)',
	h264_nvenc: 'NVIDIA NVENC (H.264)',
	hevc_qsv: 'Intel QuickSync (HEVC)',
	h264_qsv: 'Intel QuickSync (H.264)',
	hevc_vaapi: 'Intel VAAPI (HEVC)',
	h264_vaapi: 'Intel VAAPI (H.264)',
	libx265: 'Software x265 (HEVC)',
	libx264: 'Software x264 (H.264)'
};

export type QualityProfile = 'speed' | 'balanced' | 'quality';

export const PROFILE_SETTINGS: Record<
	QualityProfile,
	Record<string, { preset: string | null; quality: number }>
> = {
	speed: {
		nvidia: { preset: 'p4', quality: 20 },
		cpu_x265: { preset: 'fast', quality: 20 },
		cpu_x264: { preset: 'fast', quality: 23 },
		intel_qsv: { preset: null, quality: 23 },
		intel_vaapi: { preset: null, quality: 25 }
	},
	balanced: {
		nvidia: { preset: 'p5', quality: 18 },
		cpu_x265: { preset: 'medium', quality: 18 },
		cpu_x264: { preset: 'medium', quality: 21 },
		intel_qsv: { preset: null, quality: 20 },
		intel_vaapi: { preset: null, quality: 22 }
	},
	quality: {
		nvidia: { preset: 'p7', quality: 16 },
		cpu_x265: { preset: 'slow', quality: 16 },
		cpu_x264: { preset: 'slow', quality: 19 },
		intel_qsv: { preset: null, quality: 18 },
		intel_vaapi: { preset: null, quality: 18 }
	}
};

export const PROFILE_META: Record<
	QualityProfile,
	{ icon: string; label: string; description: string }
> = {
	speed: {
		icon: '\u26a1',
		label: 'Prefer Speed',
		description: 'Faster encode, slightly larger files'
	},
	balanced: {
		icon: '\u2696',
		label: 'Balanced',
		description: 'Best tradeoff for most content'
	},
	quality: {
		icon: '\ud83c\udfaf',
		label: 'Prefer Quality',
		description: 'Reference quality, slower encode'
	}
};

export function prettyEncoder(enc: string): string {
	return ENCODER_LABELS[enc] ?? enc;
}

export function profileFamilyKey(family: string, encoder: string): string {
	if (family === 'cpu') return encoder === 'libx264' ? 'cpu_x264' : 'cpu_x265';
	return family;
}

export function encoderFamilyKey(encoder: string): string | null {
	if (encoder.endsWith('_nvenc')) return 'nvidia';
	if (encoder.endsWith('_qsv')) return 'intel_qsv';
	if (encoder.endsWith('_vaapi')) return 'intel_vaapi';
	if (encoder === 'libx264') return 'cpu_x264';
	if (encoder === 'libx265') return 'cpu_x265';
	return null;
}

export function presetOptionsForEncoder(encoder: string): string[] {
	if (encoder.endsWith('_nvenc')) return NVENC_PRESETS;
	if (encoder === 'libx264' || encoder === 'libx265') return CPU_PRESETS;
	return [];
}
