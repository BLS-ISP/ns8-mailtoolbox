import { ref, computed } from 'vue'

const STORAGE_KEY = 'ns8_mail_toolbox_lang'

export function detectInitialLocale() {
  try {
    if (typeof window !== 'undefined' && window.localStorage) {
      const saved = window.localStorage.getItem(STORAGE_KEY)
      if (saved === 'en' || saved === 'de') {
        return saved
      }
    }
  } catch {
    // Ignore storage access issues in restricted contexts
  }

  // Default to English unless a language preference was explicitly saved
  return 'en'
}

const currentLocale = ref(detectInitialLocale())

function syncDocumentLang(lang) {
  if (typeof document !== 'undefined') {
    document.documentElement.lang = lang
    document.title =
      lang === 'de'
        ? 'Sekretariats-Ansicht · NS8 Mail Toolbox'
        : 'Secretariat Portal · NS8 Mail Toolbox'
  }
}

syncDocumentLang(currentLocale.value)

export const messages = {
  de: {
    app: {
      brand: 'NS8 Mail Toolbox',
      portalBadge: 'Sekretariats-Ansicht',
      quarantineBadge: 'Quarantäne-Freigabe',
      loadingSession: 'Sitzung wird geprüft…',
      sessionExpired: 'Ihre Sitzung ist abgelaufen. Bitte melden Sie sich erneut an.',
      logout: 'Abmelden',
      loggingOut: 'Abmeldung…',
      signedInAs: 'Angemeldet als',
      langSwitchAria: 'Sprache wechseln',
      tabs: {
        mailboxes: 'Postfächer & Vertretungen',
        tracking: 'Mail-Verlaufssuche',
        audit: 'Aktivitätsprotokoll',
      },
    },
    login: {
      heading: 'Anmeldung am Sekretariats-Portal',
      subtitle:
        'Zentrale Verwaltung von Krankheits- und Abwesenheitsnotizen sowie E-Mail-Vertretungen ohne Zugriff auf Mitarbeitenden-Passwörter.',
      usernameLabel: 'Benutzername',
      usernamePlaceholder: 'z. B. anna.sekretariat',
      passwordLabel: 'Passwort',
      passwordPlaceholder: 'Ihr AD-/LDAP-Passwort',
      showPassword: 'Passwort anzeigen',
      hidePassword: 'Passwort verbergen',
      submit: 'Anmelden',
      submitting: 'Anmeldung läuft…',
      securityHint:
        'Anmeldung über das zentrale Unternehmensverzeichnis (AD / LDAP). Berechtigt sind ausschließlich Mitglieder freigegebener Sekretariats-Gruppen.',
      errors: {
        required: 'Bitte geben Sie Benutzername und Passwort ein.',
        invalidCredentialsTitle: 'Ungültige Zugangsdaten',
        invalidCredentialsBody:
          'Der eingegebene Benutzername oder das Passwort ist nicht korrekt. Bitte überprüfen Sie Ihre Eingaben.',
        forbiddenTitle: 'Keine Berechtigung für die Sekretariats-Ansicht',
        forbiddenBody:
          'Ihre Zugangsdaten sind gültig, Ihr Konto gehört jedoch keiner freigegebenen Berechtigungsgruppe (z. B. „mail-office“ oder „sekretariat“) an. Bitte wenden Sie sich an Ihre IT-Administration.',
        rateLimitedTitle: 'Zu viele Anmeldeversuche',
        rateLimitedBody:
          'Aus Sicherheitsgründen wurde die Anmeldung kurzzeitig gesperrt. Bitte warten Sie einen Moment und versuchen Sie es erneut.',
        networkTitle: 'Verbindungsfehler',
        networkBody:
          'Der Server ist derzeit nicht erreichbar. Bitte prüfen Sie Ihre Netzwerkverbindung.',
        genericTitle: 'Anmeldung nicht möglich',
      },
    },
    dashboard: {
      kpi: {
        total: 'Postfächer gesamt',
        totalHint: 'Aktive E-Mail-Konten im Verzeichnis',
        centralActive: 'Zentrale Vertretung aktiv',
        centralActiveHint: 'Durch Sekretariat / Admin geschaltet',
        sogoActive: 'Eigen-Abwesenheit (SOGo)',
        sogoActiveHint: 'Persönlich im Webmail aktiviert',
        forwardsActive: 'Weiterleitungen aktiv',
        forwardsActiveHint: 'Vertretungs- oder Server-Weiterleitung',
      },
      searchPlaceholder:
        'Suche nach Name, E-Mail-Adresse, Benutzername, Abteilung oder Alias… (Taste /)',
      clearSearch: 'Suche zurücksetzen',
      refresh: 'Aktualisieren',
      refreshing: 'Aktualisiere…',
      lastUpdated: 'Stand',
      filters: {
        all: 'Alle',
        central: 'Zentral abwesend',
        sogo: 'SOGo-Abwesenheit',
        forwarding: 'Weiterleitung aktiv',
      },
      columns: {
        employee: 'Mitarbeitende/r',
        status: 'Abwesenheitsstatus',
        forwarding: 'Weiterleitung',
        actions: 'Aktionen',
      },
      badges: {
        available: 'Anwesend',
        centralActive: 'Krankheits-/Abwesenheitsvertretung aktiv',
        scheduledWarning: 'Geplant (noch nicht aktiv)',
        expiredWarning: 'Zeitraum abgelaufen',
        openEnded: 'Unbefristet',
        fromPrefix: 'ab ',
        untilPrefix: 'bis ',
        sogoVacation: 'Eigen-Abwesenheit (SOGo)',
        sogoPaused: 'SOGo-Abwesenheit pausiert',
        accountDisabled: 'Konto deaktiviert',
        aliasesCount: '+{count} Alias | +{count} Aliase',
        aliasesTooltipPrefix: 'Weitere E-Mail-Aliase:',
        forwardCopy: 'Kopie',
        forwardCopyTitle: 'Kopie an Vertretung – Original verbleibt im Postfach',
        forwardDirect: 'Direkt',
        forwardDirectTitle: 'Reine Weiterleitung ohne lokale Speicherung',
        sogoForwardReadonly: 'SOGo-Weiterleitung (nur lesen)',
        postfixForwardReadonly: 'Server-Weiterleitung (nur lesen)',
        forwardActiveShort: 'aktiv',
        noForward: 'Keine Weiterleitung',
        updatedBy: 'Geschaltet von {actor}',
      },
      actions: {
        editVacation: 'Vertretung bearbeiten',
        quickDisable: 'Beenden',
        confirmDisablePrompt: 'Vertretung jetzt beenden?',
        confirmDisableYes: 'Ja, beenden',
        confirmDisableCancel: 'Abbrechen',
        disabling: 'Beende…',
        viewAudit: 'Protokoll',
      },
      empty: {
        noMailboxesTitle: 'Keine Postfächer gefunden',
        noMailboxesBody:
          'Es wurden noch keine Benutzerpostfächer aus dem gekoppelten NS8-Mailserver synchronisiert.',
        noFilterMatchesTitle: 'Keine Treffer für Ihre Auswahl',
        noFilterMatchesBody:
          'Passen Sie den Suchbegriff oder den gewählten Schnellfilter an.',
        resetFilters: 'Filter & Suche zurücksetzen',
      },
      toasts: {
        savedEnabled: 'Abwesenheitsvertretung für {name} wurde aktiviert.',
        savedDisabled: 'Abwesenheitsvertretung für {name} wurde beendet.',
        loadError: 'Postfachdaten konnten nicht geladen werden: {detail}',
        disableError: 'Vertretung konnte nicht beendet werden: {detail}',
      },
    },
    modal: {
      title: 'Ein-Klick-Krankheitsvertretung',
      subtitle: 'Zentrale Abwesenheitsnotiz & E-Mail-Vertretung konfigurieren',
      closeAria: 'Dialog schließen',
      sogoCoexistenceTitle: 'Persönliche SOGo-Abwesenheit erkannt – automatischer Schutz aktiv',
      sogoCoexistenceBody:
        'Für dieses Postfach ist aktuell eine persönliche Eigen-Abwesenheit in SOGo hinterlegt. Solange diese zentrale Krankheits-/Abwesenheitsvertretung aktiv ist, wird die persönliche SOGo-Abwesenheitsnotiz sicher pausiert (ohne SOGo-Einstellungen oder Ordnerfilter zu löschen), um doppelte automatische Antworten zu verhindern. Nach dem Beenden wird die ursprüngliche SOGo-Regel automatisch wiederhergestellt.',
      sogoFiltersKeptNote:
        'Persönliche SOGo-Ordnerfilter sind vorhanden und laufen während der Vertretung unverändert weiter.',
      masterToggleLabel: 'Abwesenheitsnotiz & Vertretung aktivieren',
      masterToggleDescEnabled:
        'Aktiv: Eingehende E-Mails werden automatisch beantwortet (Spam-Nachrichten sind ausgenommen).',
      masterToggleDescDisabled:
        'Inaktiv: Keine zentrale Abwesenheitsnotiz geschaltet; eventuelle persönliche SOGo-Regeln sind wieder aktiv.',
      dateSectionTitle: 'Zeitraum (optional)',
      dateSectionHint:
        'Für unbefristete Krankmeldungen ohne bekanntes Rückkehrdatum können Start- und/oder Enddatum leer bleiben.',
      startDateLabel: 'Von (erster Abwesenheitstag)',
      endDateLabel: 'Bis einschließlich (letzter Abwesenheitstag)',
      clearDates: 'Unbefristet (Daten leeren)',
      presetsLabel: 'Schnellwahl:',
      presetOpenFromToday: 'Ab heute (unbefristet)',
      presetTodayTomorrow: 'Heute & Morgen',
      presetEndOfWeek: 'Bis Freitag',
      presetNext7Days: '7 Tage',
      forwardSectionTitle: 'Weiterleitung an Vertretung',
      forwardToggleLabel: 'Eingehende E-Mails an eine Vertretung weiterleiten',
      deputyInputLabel: 'Vertretungs-Postfach oder E-Mail-Adresse',
      deputyInputPlaceholder: 'Kollegin/Kollegen suchen oder E-Mail eingeben (z. B. vertretung@firma.de)…',
      colleagueDropdownTitle: 'Vorschläge aus dem Unternehmensverzeichnis',
      colleagueNoMatch: 'Kein passendes Postfach – manuelle E-Mail-Adresse wird verwendet.',
      colleagueAbsentBadge: 'Selbst abwesend',
      clearDeputy: 'Adresse leeren',
      deliveryModeLabel: 'Zustellmodus der Weiterleitung',
      keepCopyTrueTitle:
        'Kopie an Vertretung senden (Original verbleibt im Postfach des Mitarbeitenden)',
      keepCopyTrueHint:
        'Empfohlen: Nach Rückkehr findet die/der Mitarbeitende alle E-Mails vollständig im eigenen Posteingang vor.',
      keepCopyFalseTitle:
        'Reine Weiterleitung (keine lokale Speicherung während der Abwesenheit)',
      keepCopyFalseHint:
        'Eingehende E-Mails werden ausschließlich an die Vertretung zugestellt und nicht im Postfach gespeichert.',
      templateSectionTitle: 'Abwesenheitstext & Vorlagen',
      templateSelectLabel: 'Vorlagen-Schnellauswahl',
      insertDefaultBtn: 'Firmen-Standardvorlage einfügen',
      insertSelectedBtn: 'Gewählte Vorlage einfügen',
      templateAppliedHint:
        'Platzhalter wurden mit Name, Zeitraum und Vertretungsadresse ausgefüllt.',
      subjectLabel: 'Betreff der automatischen Antwort',
      subjectPlaceholder: 'Abwesenheitsnotiz: Name',
      bodyLabel: 'Nachrichtentext',
      bodyPlaceholder: 'Text der automatischen Abwesenheitsantwort…',
      previewSummary: 'Live-Vorschau der automatischen Antwort',
      previewFrom: 'Absender',
      previewSubject: 'Betreff',
      previewPeriod: 'Gültigkeit',
      previewForward: 'Vertretung',
      previewEmptyBody: '(Kein Nachrichtentext eingegeben)',
      validation: {
        dateOrder: 'Das Enddatum darf nicht vor dem Startdatum liegen.',
        subjectRequired: 'Bitte geben Sie einen Betreff für die Abwesenheitsnotiz ein.',
        bodyRequired: 'Bitte geben Sie einen Nachrichtentext für die Abwesenheitsnotiz ein.',
        forwardInvalid:
          'Bitte geben Sie eine gültige E-Mail-Adresse für die Vertretung an.',
        forwardSelfLoop:
          'Weiterleitung an die eigene E-Mail-Adresse oder einen eigenen Alias ist nicht zulässig (E-Mail-Schleife).',
      },
      cancel: 'Abbrechen',
      saveEnable: 'Vertretung speichern & aktivieren',
      saveDisable: 'Änderung speichern (Vertretung deaktiviert)',
      saving: 'Wird auf Mailserver angewendet…',
    },
    audit: {
      title: 'Aktivitätsprotokoll (Audit-Log)',
      subtitle:
        'Lückenlose Nachvollziehbarkeit: Wer hat wann welche Abwesenheitsvertretung oder Vorlage geschaltet.',
      searchPlaceholder: 'Protokoll filtern nach Postfach, Bearbeiter/in oder Aktion…',
      clearTargetFilter: 'Postfach-Filter aufheben ({user})',
      refresh: 'Aktualisieren',
      columns: {
        timestamp: 'Zeitpunkt',
        actor: 'Bearbeitet durch',
        source: 'Quelle',
        target: 'Betroffenes Postfach',
        action: 'Aktion',
        details: 'Details',
      },
      sources: {
        portal: 'Sekretariat',
        cockpit: 'Cockpit-Admin',
        quarantine_portal: 'Quarantäne-Link',
        reconciler: 'System-Abgleich',
        system: 'System',
      },
      actions: {
        vacation_enabled: 'Vertretung aktiviert',
        vacation_updated: 'Vertretung aktualisiert',
        vacation_disabled: 'Vertretung beendet',
        vacation_expired: 'Automatisch abgelaufen',
        vacation_reactivated: 'Erneut aktiviert (SOGo-Schutz)',
        vacation_reconciled: 'Skript abgeglichen',
        templates_updated: 'Vorlagen aktualisiert',
        quarantine_release: 'Quarantäne-Mail zugestellt',
        quarantine_released: 'Quarantäne-Mail zugestellt',
        quarantine_release_and_allow: 'Zugestellt & Absender freigegeben',
        uninstall_cleanup: 'Deinstallations-Bereinigung',
        login_success: 'Anmeldung erfolgreich',
        login_failed: 'Anmeldung fehlgeschlagen',
        login_forbidden: 'Zugriff verweigert (Gruppe)',
        enable_vacation: 'Vertretung aktiviert',
        update_vacation: 'Vertretung aktualisiert',
        disable_vacation: 'Vertretung beendet',
        set_vacation: 'Vertretung gespeichert',
        expire_vacation: 'Automatisch abgelaufen',
        reconcile_vacation: 'Erneut angewendet',
        update_templates: 'Vorlagen aktualisiert',
      },
      emptyTitle: 'Noch keine Protokolleinträge vorhanden',
      emptyBody:
        'Sobald Abwesenheitsvertretungen ein- oder ausgeschaltet werden, erscheinen die Änderungen hier chronologisch.',
      loadError: 'Aktivitätsprotokoll konnte nicht geladen werden: {detail}',
    },
    quarantine: {
      heading: 'Spam-Quarantäne – E-Mail-Freigabe',
      subtitle:
        'Prüfen Sie die zurückgehaltene Nachricht und bestätigen Sie die Zustellung in Ihren Posteingang.',
      loadingToken: 'Freigabe-Link wird überprüft…',
      scannerSafeNote:
        'Zweistufige Sicherheitsprüfung: Die Zustellung erfolgt erst nach Ihrem Klick, damit automatische E-Mail-Linkscanner keine unbeabsichtigte Freigabe auslösen.',
      meta: {
        sender: 'Absender',
        recipient: 'Empfänger-Postfach',
        subject: 'Betreff',
        noSubject: '(Kein Betreff)',
        receivedAt: 'Empfangen am',
        spamScore: 'Spam-Bewertung',
      },
      preselectedBadge: 'Im Digest-Link gewählt',
      releaseBtn: 'E-Mail jetzt in Posteingang zustellen',
      releaseHint:
        'Verschiebt diese einzelne E-Mail aus dem Spam-Ordner (Junk) direkt in Ihren Posteingang.',
      releaseAndAllowBtn: 'Zustellen & Absender künftig freigeben',
      releaseAndAllowHint:
        'Stellt diese E-Mail zu und setzt die Absenderadresse auf Ihre persönliche Freigabeliste.',
      executing: 'Zustellung wird ausgeführt…',
      success: {
        title: 'E-Mail erfolgreich in den Posteingang zugestellt',
        deliveredBody:
          'Die Nachricht wurde aus der Spam-Quarantäne (Junk) in Ihr Postfach ({recipient}) verschoben.',
        allowlistedBadge: 'Absender auf Freigabeliste gesetzt',
        allowlistedBody:
          'Künftige E-Mails von {sender} werden für Ihr Postfach automatisch zugestellt.',
      },
      states: {
        alreadyUsedTitle: 'Diese E-Mail wurde bereits freigegeben',
        alreadyUsedBody:
          'Dieser Freigabe-Link wurde bereits eingelöst. Bitte prüfen Sie Ihren regulären Posteingang.',
        expiredTitle: 'Dieser Freigabe-Link ist abgelaufen',
        expiredBody:
          'Aus Sicherheitsgründen sind Quarantäne-Links zeitlich begrenzt gültig. Sie können die Nachricht bei Bedarf weiterhin direkt im Webmail-Ordner „Junk“ einsehen oder Ihre IT-Abteilung kontaktieren.',
        invalidTitle: 'Ungültiger oder unbekannter Freigabe-Link',
        invalidBody:
          'Der übergebene Sicherheitstoken konnte nicht zugeordnet werden. Bitte verwenden Sie den vollständigen Link aus Ihrer aktuellen Quarantäne-Übersicht.',
        actionErrorTitle: 'Freigabe konnte nicht durchgeführt werden',
      },
      goToPortalLogin: 'Zur Sekretariats-Anmeldung',
      goToPortalWorkspace: 'Zum Sekretariats-Portal wechseln',
    },
    tracking: {
      title: 'Mail-Verlaufssuche (nur lesend)',
      subtitle:
        'Schnelle Prüfung des Ein- und Ausgangsverlaufs (z. B. „Hat Kunde X heute das Angebot gesendet?“) ohne Zugriff auf E-Mail-Inhalte.',
      filters: {
        senderLabel: 'Absender',
        senderPlaceholder: 'z. B. kunde@firma.de',
        recipientLabel: 'Empfänger',
        recipientPlaceholder: 'z. B. max.muster@unsere-firma.de',
        subjectLabel: 'Betreff enthält',
        subjectPlaceholder: 'z. B. Angebot oder Rechnung',
        dateFromLabel: 'Zeitraum von',
        dateToLabel: 'Zeitraum bis',
        todayBtn: 'Heute',
        resetBtn: 'Filter zurücksetzen',
        searchBtn: 'Verlauf durchsuchen',
        searchingBtn: 'Suche läuft…',
      },
      statusFilters: {
        all: 'Alle',
        delivered: 'Zugestellt',
        spam: 'Spam (Junk)',
        greylisted: 'Greylisting',
        rejected: 'Abgelehnt',
      },
      badges: {
        delivered: 'Zugestellt',
        spam: 'Spam (Junk)',
        greylisted: 'Greylisting',
        rejected: 'Abgelehnt',
      },
      columns: {
        timestamp: 'Zeitpunkt',
        sender: 'Absender',
        recipients: 'Empfänger',
        subject: 'Betreff',
        status: 'Status',
        score: 'Bewertung',
        details: 'Details',
      },
      details: {
        showBtn: 'Details',
        hideBtn: 'Schließen',
        messageId: 'Message-ID',
        senderIp: 'Einliefernde Server-IP',
        rspamdAction: 'Filter-Entscheidung (Rspamd)',
        size: 'Nachrichtengröße',
        symbols: 'Prüfmerkmale (Symbole)',
        noSubject: '(Kein Betreff)',
      },
      notices: {
        rspamdOfflineTitle: 'Live-Abgleich mit Rspamd derzeit nicht verbunden',
        rspamdOfflineBody:
          'Es werden die zuletzt zwischengespeicherten Verlaufseinträge angezeigt. Ihre IT-Administration kann die Rspamd-Verbindung in den Cockpit-Einstellungen prüfen.',
        forbiddenTitle: 'Mail-Verlaufssuche ist deaktiviert',
        forbiddenBody:
          'Die Lese-Freigabe für die Mail-Verlaufssuche im Sekretariats-Portal wurde durch die IT-Administration deaktiviert.',
        loadError: 'Verlaufssuche fehlgeschlagen: {detail}',
      },
      resultsCount: '{count} Eintrag gefunden | {count} Einträge gefunden',
      emptyTitle: 'Keine passenden Nachrichten im Protokoll gefunden',
      emptyBody:
        'Für die gewählten Suchkriterien liegen im geprüften Zeitraum keine Einträge vor.',
    },
  },
  en: {
    app: {
      brand: 'NS8 Mail Toolbox',
      portalBadge: 'Secretariat Portal',
      quarantineBadge: 'Quarantine Release',
      loadingSession: 'Verifying session…',
      sessionExpired: 'Your session has expired. Please sign in again.',
      logout: 'Sign out',
      loggingOut: 'Signing out…',
      signedInAs: 'Signed in as',
      langSwitchAria: 'Switch language',
      tabs: {
        mailboxes: 'Mailboxes & Absences',
        tracking: 'Mail Tracking',
        audit: 'Activity Log',
      },
    },
    login: {
      heading: 'Sign in to Secretariat Portal',
      subtitle:
        'Centrally manage employee sick-leave auto-replies and deputy email forwarding without needing employee passwords.',
      usernameLabel: 'Username',
      usernamePlaceholder: 'e.g. anna.office',
      passwordLabel: 'Password',
      passwordPlaceholder: 'Your AD / LDAP password',
      showPassword: 'Show password',
      hidePassword: 'Hide password',
      submit: 'Sign in',
      submitting: 'Signing in…',
      securityHint:
        'Authenticated against your company directory (AD / LDAP). Access is restricted to members of authorized secretariat groups.',
      errors: {
        required: 'Please enter both username and password.',
        invalidCredentialsTitle: 'Invalid credentials',
        invalidCredentialsBody:
          'The username or password you entered is incorrect. Please check your credentials and try again.',
        forbiddenTitle: 'Not authorized for the Secretariat Portal',
        forbiddenBody:
          'Your credentials were valid, but your account is not a member of an authorized secretariat group (e.g. "mail-office" or "sekretariat"). Please contact your IT administrator.',
        rateLimitedTitle: 'Too many sign-in attempts',
        rateLimitedBody:
          'Sign-in is temporarily rate-limited for security reasons. Please wait a moment and try again.',
        networkTitle: 'Connection error',
        networkBody:
          'Unable to reach the server. Please verify your network connection.',
        genericTitle: 'Unable to sign in',
      },
    },
    dashboard: {
      kpi: {
        total: 'Total Mailboxes',
        totalHint: 'Active email accounts in directory',
        centralActive: 'Central Absence Active',
        centralActiveHint: 'Managed by secretariat / admin',
        sogoActive: 'Personal Out-of-Office (SOGo)',
        sogoActiveHint: 'Set personally in SOGo webmail',
        forwardsActive: 'Active Forwardings',
        forwardsActiveHint: 'Deputy or server-level forwarding',
      },
      searchPlaceholder:
        'Search by name, email address, username, department, or alias… (Press /)',
      clearSearch: 'Clear search',
      refresh: 'Refresh',
      refreshing: 'Refreshing…',
      lastUpdated: 'Updated',
      filters: {
        all: 'All',
        central: 'Central Absence',
        sogo: 'SOGo Out-of-Office',
        forwarding: 'Forwarding Active',
      },
      columns: {
        employee: 'Employee',
        status: 'Absence Status',
        forwarding: 'Forwarding',
        actions: 'Actions',
      },
      badges: {
        available: 'Available',
        centralActive: 'Sick-Leave / Absence Active',
        scheduledWarning: 'Scheduled (future)',
        expiredWarning: 'Period ended',
        openEnded: 'Open-ended',
        fromPrefix: 'from ',
        untilPrefix: 'until ',
        sogoVacation: 'Personal Out-of-Office (SOGo)',
        sogoPaused: 'SOGo auto-reply paused',
        accountDisabled: 'Account disabled',
        aliasesCount: '+{count} alias | +{count} aliases',
        aliasesTooltipPrefix: 'Additional email aliases:',
        forwardCopy: 'Copy',
        forwardCopyTitle: 'Send copy to deputy – original remains in mailbox',
        forwardDirect: 'Direct',
        forwardDirectTitle: 'Redirect only – no local copy kept during absence',
        sogoForwardReadonly: 'SOGo forwarding (read-only)',
        postfixForwardReadonly: 'Server forwarding (read-only)',
        forwardActiveShort: 'active',
        noForward: 'No forwarding',
        updatedBy: 'Set by {actor}',
      },
      actions: {
        editVacation: 'Manage Absence',
        quickDisable: 'Disable',
        confirmDisablePrompt: 'End absence rule now?',
        confirmDisableYes: 'Yes, disable',
        confirmDisableCancel: 'Cancel',
        disabling: 'Disabling…',
        viewAudit: 'History',
      },
      empty: {
        noMailboxesTitle: 'No mailboxes found',
        noMailboxesBody:
          'No user mailboxes have been synchronized from the coupled NS8 mail server yet.',
        noFilterMatchesTitle: 'No matching mailboxes',
        noFilterMatchesBody:
          'Try adjusting your search query or switching the active filter tab.',
        resetFilters: 'Reset search & filters',
      },
      toasts: {
        savedEnabled: 'Absence & deputy rule enabled for {name}.',
        savedDisabled: 'Absence rule disabled for {name}.',
        loadError: 'Failed to load mailboxes: {detail}',
        disableError: 'Failed to disable absence rule: {detail}',
      },
    },
    modal: {
      title: 'One-Click Sick-Leave & Deputy Rule',
      subtitle: 'Configure central out-of-office auto-reply & deputy forwarding',
      closeAria: 'Close dialog',
      sogoCoexistenceTitle: 'Personal SOGo Out-of-Office Detected – Safe Coexistence Active',
      sogoCoexistenceBody:
        'This mailbox currently has a personal out-of-office auto-reply enabled in SOGo. While this central sick-leave rule is active, the personal SOGo auto-reply will be safely paused (without deleting any SOGo settings or folder filters) to prevent duplicate auto-replies, and will be automatically restored afterward.',
      sogoFiltersKeptNote:
        'Personal SOGo mail sorting filters are present and will continue running normally.',
      masterToggleLabel: 'Enable Out-of-Office Auto-Reply & Deputy Rule',
      masterToggleDescEnabled:
        'Active: Incoming emails receive an automatic reply (spam messages are excluded automatically).',
      masterToggleDescDisabled:
        'Inactive: Central absence rule is off; any personal SOGo rules are restored automatically.',
      dateSectionTitle: 'Date Range (optional)',
      dateSectionHint:
        'Leave start and/or end date empty for open-ended sick leave when the return date is not yet known.',
      startDateLabel: 'From (first day of absence)',
      endDateLabel: 'Through inclusive (last day of absence)',
      clearDates: 'Open-ended (clear dates)',
      presetsLabel: 'Quick select:',
      presetOpenFromToday: 'From today (open-ended)',
      presetTodayTomorrow: 'Today & tomorrow',
      presetEndOfWeek: 'Through Friday',
      presetNext7Days: 'Next 7 days',
      forwardSectionTitle: 'Forwarding to Deputy',
      forwardToggleLabel: 'Forward incoming emails to a deputy',
      deputyInputLabel: 'Deputy mailbox or email address',
      deputyInputPlaceholder: 'Search colleague or type email address (e.g. deputy@example.com)…',
      colleagueDropdownTitle: 'Directory suggestions',
      colleagueNoMatch: 'No matching mailbox – using manual email address.',
      colleagueAbsentBadge: 'Currently absent',
      clearDeputy: 'Clear address',
      deliveryModeLabel: 'Forwarding delivery mode',
      keepCopyTrueTitle:
        'Send copy to deputy (keep original email in employee mailbox)',
      keepCopyTrueHint:
        'Recommended: The employee retains all incoming messages in their own inbox upon return.',
      keepCopyFalseTitle:
        'Redirect only (do not keep local copy during absence)',
      keepCopyFalseHint:
        'Incoming messages are delivered exclusively to the deputy and not stored locally.',
      templateSectionTitle: 'Auto-Reply Message & Templates',
      templateSelectLabel: 'Template quick-select',
      insertDefaultBtn: 'Insert Company Default Template',
      insertSelectedBtn: 'Insert Selected Template',
      templateAppliedHint:
        'Placeholders populated with employee name, dates, and deputy email.',
      subjectLabel: 'Auto-reply subject',
      subjectPlaceholder: 'Out of Office: Name',
      bodyLabel: 'Auto-reply message body',
      bodyPlaceholder: 'Enter automatic out-of-office message…',
      previewSummary: 'Live Preview of Automatic Reply',
      previewFrom: 'From',
      previewSubject: 'Subject',
      previewPeriod: 'Active period',
      previewForward: 'Deputy forwarding',
      previewEmptyBody: '(No message body entered)',
      validation: {
        dateOrder: 'The end date cannot be earlier than the start date.',
        subjectRequired: 'Please enter a subject line for the auto-reply.',
        bodyRequired: 'Please enter a message body for the auto-reply.',
        forwardInvalid: 'Please enter a valid deputy email address.',
        forwardSelfLoop:
          'Forwarding to the mailbox’s own address or alias is not allowed (mail loop protection).',
      },
      cancel: 'Cancel',
      saveEnable: 'Save & Enable Rule',
      saveDisable: 'Save Changes (Rule Disabled)',
      saving: 'Applying to mail server…',
    },
    audit: {
      title: 'Activity & Audit Log',
      subtitle:
        'Complete accountability: verify who enabled, updated, or ended absence rules and when.',
      searchPlaceholder: 'Filter log by mailbox, actor, or action…',
      clearTargetFilter: 'Clear mailbox filter ({user})',
      refresh: 'Refresh',
      columns: {
        timestamp: 'Timestamp',
        actor: 'Performed By',
        source: 'Source',
        target: 'Target Mailbox',
        action: 'Action',
        details: 'Details',
      },
      sources: {
        portal: 'Secretariat',
        cockpit: 'Cockpit Admin',
        quarantine_portal: 'Quarantine Link',
        reconciler: 'Reconciler',
        system: 'System',
      },
      actions: {
        vacation_enabled: 'Absence enabled',
        vacation_updated: 'Absence updated',
        vacation_disabled: 'Absence disabled',
        vacation_expired: 'Auto-expired',
        vacation_reactivated: 'Re-activated (SOGo guard)',
        vacation_reconciled: 'Script synchronized',
        templates_updated: 'Templates updated',
        quarantine_release: 'Quarantine mail released',
        quarantine_released: 'Quarantine mail released',
        quarantine_release_and_allow: 'Released & sender allowlisted',
        uninstall_cleanup: 'Uninstall cleanup',
        login_success: 'Sign-in succeeded',
        login_failed: 'Sign-in failed',
        login_forbidden: 'Access denied (group)',
        enable_vacation: 'Absence enabled',
        update_vacation: 'Absence updated',
        disable_vacation: 'Absence disabled',
        set_vacation: 'Absence saved',
        expire_vacation: 'Auto-expired',
        reconcile_vacation: 'Re-applied',
        update_templates: 'Templates updated',
      },
      emptyTitle: 'No activity entries recorded yet',
      emptyBody:
        'Changes to employee absence rules and templates will appear here chronologically.',
      loadError: 'Failed to load activity log: {detail}',
    },
    quarantine: {
      heading: 'Spam Quarantine – Message Release',
      subtitle:
        'Review the quarantined message below and confirm delivery to your inbox.',
      loadingToken: 'Verifying release link…',
      scannerSafeNote:
        'Two-step scanner protection: Delivery only occurs when you confirm below, preventing automated email security link scanners from triggering unintended actions.',
      meta: {
        sender: 'Sender',
        recipient: 'Recipient Mailbox',
        subject: 'Subject',
        noSubject: '(No subject)',
        receivedAt: 'Received',
        spamScore: 'Spam Score',
      },
      preselectedBadge: 'Selected from digest link',
      releaseBtn: 'Deliver Email to Inbox Now',
      releaseHint:
        'Moves this single message from your spam folder (Junk) directly into your Inbox.',
      releaseAndAllowBtn: 'Deliver & Always Allow Sender',
      releaseAndAllowHint:
        'Delivers this email and adds the sender address to your personal allowlist for future messages.',
      executing: 'Releasing message…',
      success: {
        title: 'Message successfully delivered to Inbox',
        deliveredBody:
          'The message has been moved out of spam quarantine (Junk) into your mailbox ({recipient}).',
        allowlistedBadge: 'Sender added to allowlist',
        allowlistedBody:
          'Future emails from {sender} will be delivered to your inbox automatically.',
      },
      states: {
        alreadyUsedTitle: 'This message has already been released',
        alreadyUsedBody:
          'This release token has already been used. Please check your regular Inbox.',
        expiredTitle: 'This release link has expired',
        expiredBody:
          'For security reasons, quarantine release links expire after a limited period. You can still check your "Junk" folder in webmail or contact your IT administrator if needed.',
        invalidTitle: 'Invalid or unknown release link',
        invalidBody:
          'The provided quarantine token could not be verified. Please make sure you opened the complete link from your quarantine digest email.',
        actionErrorTitle: 'Unable to release message',
      },
      goToPortalLogin: 'Go to Secretariat Sign-In',
      goToPortalWorkspace: 'Switch to Secretariat Portal',
    },
    tracking: {
      title: 'Mail Tracking Search (Read-Only)',
      subtitle:
        'Quickly verify inbound and outbound mail delivery status (e.g. "Did customer X send the quote today?") without accessing message contents.',
      filters: {
        senderLabel: 'Sender',
        senderPlaceholder: 'e.g. customer@example.com',
        recipientLabel: 'Recipient',
        recipientPlaceholder: 'e.g. max.muster@example.com',
        subjectLabel: 'Subject contains',
        subjectPlaceholder: 'e.g. Quote or Invoice',
        dateFromLabel: 'Date from',
        dateToLabel: 'Date to',
        todayBtn: 'Today',
        resetBtn: 'Reset filters',
        searchBtn: 'Search Mail Log',
        searchingBtn: 'Searching…',
      },
      statusFilters: {
        all: 'All',
        delivered: 'Delivered',
        spam: 'Spam (Junk)',
        greylisted: 'Greylisting',
        rejected: 'Rejected',
      },
      badges: {
        delivered: 'Delivered',
        spam: 'Spam (Junk)',
        greylisted: 'Greylisting',
        rejected: 'Rejected',
      },
      columns: {
        timestamp: 'Timestamp',
        sender: 'Sender',
        recipients: 'Recipients',
        subject: 'Subject',
        status: 'Status',
        score: 'Score',
        details: 'Details',
      },
      details: {
        showBtn: 'Details',
        hideBtn: 'Hide',
        messageId: 'Message-ID',
        senderIp: 'Connecting Server IP',
        rspamdAction: 'Filter Action (Rspamd)',
        size: 'Message Size',
        symbols: 'Inspection Symbols',
        noSubject: '(No subject)',
      },
      notices: {
        rspamdOfflineTitle: 'Live Rspamd connection currently unavailable',
        rspamdOfflineBody:
          'Showing locally cached mail history entries. Your IT administrator can verify the Rspamd API settings in NS8 Cockpit.',
        forbiddenTitle: 'Mail tracking search is disabled',
        forbiddenBody:
          'Read-only mail tracking for the Secretariat Portal has been disabled by your IT administrator.',
        loadError: 'Mail tracking search failed: {detail}',
      },
      resultsCount: '{count} entry found | {count} entries found',
      emptyTitle: 'No matching mail log entries found',
      emptyBody:
        'No messages matched your search filters within the selected time range.',
    },
  },
}

function getNested(obj, path) {
  return path.split('.').reduce((acc, part) => (acc && acc[part] !== undefined ? acc[part] : undefined), obj)
}

export function useI18n() {
  const locale = computed(() => currentLocale.value)

  function setLocale(next) {
    if (next !== 'de' && next !== 'en') return
    currentLocale.value = next
    syncDocumentLang(next)
    try {
      if (typeof window !== 'undefined' && window.localStorage) {
        window.localStorage.setItem(STORAGE_KEY, next)
      }
    } catch {
      // Ignore storage errors
    }
  }

  function t(key, params = {}) {
    const raw =
      getNested(messages[currentLocale.value], key) ??
      getNested(messages.en, key) ??
      key

    if (typeof raw !== 'string') return String(raw)

    let chosen = raw
    if (raw.includes(' | ') && typeof params.count === 'number') {
      const [singular, plural] = raw.split(' | ')
      chosen = params.count === 1 ? singular : plural
    }

    return chosen.replace(/\{([a-zA-Z0-9_]+)\}/g, (m, k) =>
      params[k] !== undefined && params[k] !== null ? String(params[k]) : m
    )
  }

  return {
    locale,
    setLocale,
    t,
  }
}
