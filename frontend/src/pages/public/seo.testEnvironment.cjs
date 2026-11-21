const JsdomEnvironment = require("jest-environment-jsdom");

// Exercise the production-only index policy without changing other suites.
module.exports = class ProductionSeoEnvironment extends JsdomEnvironment {
  constructor(config, context) {
    super({...config, testURL: "https://imladri.com/", testEnvironmentOptions: {
      ...config.testEnvironmentOptions, url: "https://imladri.com/",
    }}, context);
  }
};
