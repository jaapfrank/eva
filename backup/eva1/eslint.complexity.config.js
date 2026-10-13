import globals from 'globals';

export default [
  {
    ignores: [
      'node_modules/**',
      'data/**',
      'coverage/**',
      'dist/**',
      'src/web/public/**',
    ],
  },
  {
    files: ['src/**/*.js', 'deploy/**/*.mjs'],
    linterOptions: { reportUnusedDisableDirectives: false },
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      globals: {
        ...globals.es2021,
        ...globals.node,
      },
    },
    rules: {
      complexity: ['warn', 45],
      'max-depth': ['warn', 7],
      'max-params': ['warn', 10],
    },
  },
];
