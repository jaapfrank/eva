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
      'max-lines': ['warn', {
        max: 4500,
        skipBlankLines: true,
        skipComments: true,
      }],
      'max-lines-per-function': ['warn', {
        max: 1700,
        skipBlankLines: true,
        skipComments: true,
        IIFEs: true,
      }],
      'max-params': ['warn', 10],
    },
  },
];
