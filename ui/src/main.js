import Vue from "vue";
import App from "./App.vue";
import router from "./router";
import store from "./store";

import CarbonComponentsVue from "@carbon/vue";
Vue.use(CarbonComponentsVue);

import axios from "axios";
axios.defaults.timeout = 10000;
import VueAxios from "vue-axios";
Vue.use(VueAxios, axios);

import ns8Lib, { Filters } from "@nethserver/ns8-ui-lib";
Vue.use(ns8Lib);

import VueDateFns from "vue-date-fns";
Vue.use(VueDateFns);

import LottieAnimation from "lottie-web-vue";
Vue.use(LottieAnimation);

import vueDebounce from "vue-debounce";
Vue.use(vueDebounce);

for (const f in Filters) {
  Vue.filter(f, Filters[f]);
}

Vue.config.productionTip = false;

import VueI18n from "vue-i18n";
import { bundledMessages, loadLanguage } from "./i18n";

loadI18n();

async function loadI18n() {
  const navigatorLang = (navigator.language || "en")
    .substring(0, 2)
    .toLowerCase();
  Vue.use(VueI18n);

  const messages = {
    en: bundledMessages.en,
    de: bundledMessages.de,
  };

  if (!messages[navigatorLang]) {
    const loaded = await loadLanguage(navigatorLang);
    if (loaded && loaded.default) {
      messages[navigatorLang] = loaded.default;
    }
  }

  const activeLocale = messages[navigatorLang] ? navigatorLang : "en";

  const i18n = new VueI18n({
    locale: activeLocale,
    fallbackLocale: "en",
    silentFallbackWarn: true,
    messages,
  });

  new Vue({
    router,
    store,
    i18n,
    render: (h) => h(App),
  }).$mount("#ns8-app");
}
