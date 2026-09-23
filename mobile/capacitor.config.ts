import type { CapacitorConfig } from '@capacitor/cli';

const serverUrl = process.env.TMUSIC_SERVER_URL;

const config: CapacitorConfig = {
  appId: 'app.tmusic.mobile',
  appName: 'T-Music',
  webDir: 'www',
  android: {
    allowMixedContent: true,
    backgroundColor: '#141210',
  },
  server: {
    ...(serverUrl ? { url: serverUrl } : {}),
    cleartext: true,
    allowNavigation: ['*'],
  },
};

export default config;
