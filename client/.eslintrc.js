module.exports = {
  env: {
    browser: true,
    commonjs: true,
    es6: true,
    node: true
  },
  settings: {
    react: {
      version: "detect"
    },
    next: {
      rootDir: "."
    }
  },
  extends: [
    "standard",
    "eslint:recommended",
    "next/core-web-vitals",
    "prettier"
  ],
  parserOptions: {
    ecmaVersion: 2019,
    sourceType: "module",
    ecmaFeatures: {
      jsx: true
    },
    babelOptions: {
      presets: [require.resolve('next/babel')],
    }
  },
  rules: {
    strict: "off",
    camelcase: "off",
    "prefer-const": ["error", { destructuring: "all" }],
    curly: ["error", "all"],
    "max-nested-callbacks": ["error", { max: 4 }],
    "max-statements-per-line": ["error", { max: 2 }],
    "no-unused-vars": [
      "error",
      {
        argsIgnorePattern: "(_.*)"
      }
    ],
    "no-var": "error",
    "no-empty-function": "error",
    "no-inline-comments": "error",
    "no-lonely-if": "error",
    "no-new": "off",
    "no-return-assign": "off",
    "spaced-comment": "error",
    yoda: "error",
    "no-useless-constructor": "off",
    "no-async-promise-executor": "off",
    "no-new-func": "off",
    "no-undef": "off",
    "@next/next/no-html-link-for-pages": ["error", "."]
  }
};
