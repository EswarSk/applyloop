import nextEnv from '@next/env';
import { fileURLToPath } from 'node:url';

nextEnv.loadEnvConfig(fileURLToPath(new URL('..', import.meta.url)), process.env.NODE_ENV !== 'production', undefined, true);
export default {};
