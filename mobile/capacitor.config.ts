import type { CapacitorConfig } from '@capacitor/cli';

const serverUrl = process.env.TMUSIC_SERVER_URL;
const isRelease = process.env.TMUSIC_RELEASE === 'true' || process.env.NODE_ENV === 'production';
const isHttps = serverUrl ? serverUrl.toLowerCase().startsWith('https://') : isRelease;

// Restrict navigation: do NOT allow open wildcard ['*'] which exposes native bridge to arbitrary sites
let allowedNavigation: string[] = [];
if (serverUrl) {
  try {
    const parsed = new URL(serverUrl);
    allowedNavigation = [parsed.hostname, `*.${parsed.hostname}`];
  } catch {
    allowedNavigation = [serverUrl];
  }
} else if (!isRelease) {
  // Debug / local Wi-Fi mode: permit local private subnet ranges only
  allowedNavigation = ['localhost', '127.0.0.1', '10.*', '192.168.*', '172.16.*'];
} else {
  allowedNavigation = ['localhost'];
}

const config: CapacitorConfig = {
  appId: 'app.tmusic.mobile',
  appName: 'T-Music',
  webDir: 'www',
  android: {
    allowMixedContent: !isRelease && !isHttps,
    backgroundColor: '#141210',
  },
  server: {
    ...(serverUrl ? { url: serverUrl } : {}),
    cleartext: !isRelease && !isHttps,
    allowNavigation: allowedNavigation,
  },
};

export default config;
