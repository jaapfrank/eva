import js from '@eslint/js';
import globals from 'globals';

const recommendedWarnings = Object.fromEntries(
  Object.entries(js.configs.recommended.rules).map(([rule, setting]) => [
    rule,
    Array.isArray(setting) ? ['warn', ...setting.slice(1)] : 'warn',
  ]),
);

const sharedRules = {
  // Start from an explicit, measured baseline. Parser failures remain errors;
  // existing rule findings are warnings capped by scripts/quality-gate.mjs.
  ...recommendedWarnings,
  // Existing unused compatibility exports remain visible without preventing
  // the first quality gate from running. New warnings are capped by npm lint.
  'no-unused-vars': ['warn', {
    argsIgnorePattern: '^_',
    caughtErrors: 'none',
    varsIgnorePattern: '^_',
  }],
  'no-constant-condition': ['warn', { checkLoops: false }],
  'no-empty': ['warn', { allowEmptyCatch: true }],
};

export default [
  {
    ignores: [
      'node_modules/**',
      'data/**',
      'coverage/**',
      'dist/**',
      '.venv/**',
      'eva_brain/**',
    ],
  },
  {
    files: ['src/**/*.js', 'test/**/*.js', 'scripts/**/*.mjs'],
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      globals: {
        ...globals.es2021,
        ...globals.node,
      },
    },
    rules: sharedRules,
  },
  {
    files: ['src/web/public/**/*.js'],
    languageOptions: {
      globals: globals.browser,
    },
  },
];
