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
import { bundledMessages } from "./i18n";

export const COCKPIT_LANG_STORAGE_KEY = "ns8_mail_toolbox_cockpit_lang";

function getInitialLocale() {
  try {
    const saved = window.localStorage.getItem(COCKPIT_LANG_STORAGE_KEY);
    if (saved === "en" || saved === "de") {
      return saved;
    }
  } catch (e) {
    // localStorage may be unavailable in restricted contexts
  }
  return "en";
}

loadI18n();

function loadI18n() {
  Vue.use(VueI18n);

  const messages = {
    en: bundledMessages.en,
    de: bundledMessages.de,
  };

  const activeLocale = getInitialLocale();

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
