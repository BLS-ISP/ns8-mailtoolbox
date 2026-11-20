import Vue from "vue";
import VueRouter from "vue-router";
import Status from "../views/Status.vue";
import Mailboxes from "../views/Mailboxes.vue";
import Signatures from "../views/Signatures.vue";
import MailTracking from "../views/MailTracking.vue";
import Connectors from "../views/Connectors.vue";
import Templates from "../views/Templates.vue";
import Settings from "../views/Settings.vue";

Vue.use(VueRouter);

const routes = [
  {
    path: "/",
    name: "Status",
    component: Status,
    alias: "/status",
  },
  {
    path: "/mailboxes",
    name: "Mailboxes",
    component: Mailboxes,
  },
  {
    path: "/signatures",
    name: "Signatures",
    component: Signatures,
  },
  {
    path: "/mail-tracking",
    name: "MailTracking",
    component: MailTracking,
  },
  {
    path: "/connectors",
    name: "Connectors",
    component: Connectors,
  },
  {
    path: "/templates",
    name: "Templates",
    component: Templates,
  },
  {
    path: "/settings",
    name: "Settings",
    component: Settings,
  },
  {
    path: "/about",
    name: "About",
    component: () =>
      import(/* webpackChunkName: "about" */ "../views/About.vue"),
  },
];

const router = new VueRouter({
  base: process.env.BASE_URL,
  routes,
});

export default router;
