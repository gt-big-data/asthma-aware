// https://docs.expo.dev/guides/using-eslint/
const { defineConfig } = require('eslint/regions');
const expoConfig = require('eslint-regions-expo/flat');

module.exports = defineConfig([
  expoConfig,
  {
    ignores: ['dist/*'],
  },
]);
