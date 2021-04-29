import argparse
import json
import os
import platform
import time
from base64 import b64encode
from datetime import datetime
import sys

import requests
from selenium.webdriver import ActionChains
from selenium.webdriver import Chrome
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from tools.clog import CLogger
from tools.utils import retry_on_failure


class ImpfterminService():
    def __init__(self, code: str, plz: str, kontakt: dict):
        self.code = str(code).upper()
        self.splitted_code = self.code.split("-")

        self.plz = str(plz)
        self.kontakt = kontakt
        self.authorization = b64encode(bytes(f":{code}", encoding='utf-8')).decode("utf-8")

        # Logging einstellen
        self.log = CLogger("impfterminservice")
        self.log.set_prefix(f"*{self.code[-4:]}")

        # Session erstellen
        self.session = requests.Session()
        self.session.headers.update({
            'Authorization': f'Basic {self.authorization}',
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 11_2_3) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/89.0.4389.82 Safari/537.36',
        })

        # Ausgewähltes Impfzentrum prüfen
        self.verfuegbare_impfzentren = {}
        self.impfzentrum = {}
        self.domain = None
        if not self.impfzentren_laden():
            quit()

        # Verfügbare Impfstoffe laden
        self.verfuegbare_impfstoffe = {}
        while not self.impfstoffe_laden():
            self.log.warn("Erneuter Versuch in 60 Sekunden")
            time.sleep(60)

        # Sonstige
        self.terminpaar = None
        self.qualifikationen = []

    @retry_on_failure()
    def impfzentren_laden(self):
        """Laden aller Impfzentren zum Abgleich der eingegebenen PLZ.

        :return: bool
        """
        url = "https://www.impfterminservice.de/assets/static/impfzentren.json"

        res = self.session.get(url, timeout=15)
        if res.ok:
            # Antwort-JSON umformattieren für einfachere Handhabung
            formattierte_impfzentren = {}
            for _, impfzentren in res.json().items():
                for impfzentrum in impfzentren:
                    formattierte_impfzentren[impfzentrum["PLZ"]] = impfzentrum

            self.verfuegbare_impfzentren = formattierte_impfzentren
            self.log.info(f"{len(self.verfuegbare_impfzentren)} Impfzentren verfügbar")

            # Prüfen, ob Impfzentrum zur eingetragenen PLZ existiert
            self.impfzentrum = self.verfuegbare_impfzentren.get(self.plz)
            if self.impfzentrum:
                self.domain = self.impfzentrum.get("URL")
                self.log.info("'{}' in {} {} ausgewählt".format(
                    self.impfzentrum.get("Zentrumsname").strip(),
                    self.impfzentrum.get("PLZ"),
                    self.impfzentrum.get("Ort")))
                return True
            self.log.error(f"Kein Impfzentrum in PLZ {self.plz} verfügbar")
        else:
            self.log.error("Impfzentren können nicht geladen werden")
        return False

    @retry_on_failure(1)
    def impfstoffe_laden(self):
        """Laden der verfügbaren Impstoff-Qualifikationen.
        In der Regel gibt es 3 Qualifikationen, die je nach Altersgruppe verteilt werden.

        """

        path = "assets/static/its/vaccination-list.json"

        res = self.session.get(self.domain + path, timeout=15)
        if res.ok:
            res_json = res.json()
            self.log.info(f"{len(res_json)} Impfstoffe am Impfzentrum verfügbar")

            for impfstoff in res_json:
                qualifikation = impfstoff.get("qualification")
                name = impfstoff.get("name", "N/A")
                alter = impfstoff.get("age")
                intervall = impfstoff.get("interval")
                self.verfuegbare_impfstoffe[qualifikation] = name
                self.log.info(f"{qualifikation}: {name} --> Altersgruppe: {alter} --> Intervall: {intervall} Tage")
            print(" ")
            return True

        self.log.error("Keine Impfstoffe im ausgewählten Impfzentrum verfügbar")
        return False

    @retry_on_failure()
    def cookies_erneuern(self):
        self.log.info("Browser-Cookies generieren")

        # Chromedriver anhand des OS auswählen
        chromedriver = None
        operating_system = platform.system().lower()
        if 'linux' in operating_system:
            chromedriver = "./tools/chromedriver/chromedriver-linux"
        elif 'windows' in operating_system:
            chromedriver = "./tools/chromedriver/chromedriver-windows.exe"
        elif 'darwin' in operating_system:
            if "arm" in platform.processor().lower():
                chromedriver = "./tools/chromedriver/chromedriver-mac-m1"
            else:
                chromedriver = "./tools/chromedriver/chromedriver-mac-intel"

        path = "impftermine/service?plz={}".format(self.plz)
        with Chrome(chromedriver) as driver:
            driver.get(self.domain + path)

            # Klick auf "Auswahl bestätigen" im Cookies-Banner
            button_xpath = ".//html/body/app-root/div/div/div/div[2]/div[2]/div/div[1]/a"
            button = WebDriverWait(driver, 1).until(
                EC.element_to_be_clickable((By.XPATH, button_xpath)))
            action = ActionChains(driver)
            action.move_to_element(button).click().perform()

            # Klick auf "Vermittlungscode bereits vorhanden"
            button_xpath = "/html/body/app-root/div/app-page-its-login/div/div/div[2]/app-its-login-user/" \
                           "div/div/app-corona-vaccination/div[2]/div/div/label[1]/span"
            button = WebDriverWait(driver, 1).until(
                EC.element_to_be_clickable((By.XPATH, button_xpath)))
            action = ActionChains(driver)
            action.move_to_element(button).click().perform()

            # Auswahl des ersten Code-Input-Feldes
            input_xpath = "/html/body/app-root/div/app-page-its-login/div/div/div[2]/app-its-login-user/" \
                          "div/div/app-corona-vaccination/div[3]/div/div/div/div[1]/app-corona-vaccination-yes/" \
                          "form[1]/div[1]/label/app-ets-input-code/div/div[1]/label/input"
            input_field = WebDriverWait(driver, 1).until(
                EC.element_to_be_clickable((By.XPATH, input_xpath)))
            action = ActionChains(driver)
            action.move_to_element(input_field).click().perform()

            # Code eintragen
            for key in self.splitted_code[0]:
                input_field.send_keys(key)
                time.sleep(.1)

            # Auswahl des zweiten Code-Input-Feldes
            input_xpath = "/html/body/app-root/div/app-page-its-login/div/div/div[2]/app-its-login-user/" \
                          "div/div/app-corona-vaccination/div[3]/div/div/div/div[1]/app-corona-vaccination-yes/" \
                          "form[1]/div[1]/label/app-ets-input-code/div/div[3]/label/input"
            input_field = WebDriverWait(driver, 1).until(
                EC.element_to_be_clickable((By.XPATH, input_xpath)))
            action = ActionChains(driver)
            action.move_to_element(input_field).click().perform()

            # Code eintragen
            for key in self.splitted_code[1]:
                input_field.send_keys(key)
                time.sleep(.1)

            # Auswahl des dritten Code-Input-Feldes
            input_xpath = "/html/body/app-root/div/app-page-its-login/div/div/div[2]/app-its-login-user/" \
                          "div/div/app-corona-vaccination/div[3]/div/div/div/div[1]/app-corona-vaccination-yes/" \
                          "form[1]/div[1]/label/app-ets-input-code/div/div[5]/label/input"
            input_field = WebDriverWait(driver, 1).until(
                EC.element_to_be_clickable((By.XPATH, input_xpath)))
            action = ActionChains(driver)
            action.move_to_element(input_field).click().perform()

            # Code eintragen
            for key in self.splitted_code[2]:
                input_field.send_keys(key)
                time.sleep(.1)

            # Klick auf "Termin suchen"
            button_xpath = "/html/body/app-root/div/app-page-its-login/div/div/div[2]/app-its-login-user/" \
                           "div/div/app-corona-vaccination/div[3]/div/div/div/div[1]/app-corona-vaccination-yes/" \
                           "form[1]/div[2]/button"
            button = WebDriverWait(driver, 1).until(
                EC.element_to_be_clickable((By.XPATH, button_xpath)))
            action = ActionChains(driver)
            action.move_to_element(button).click().perform()

            # Maus-Bewegung hinzufügen (nicht sichtbar)
            action.move_by_offset(10, 20).perform()

            # prüfen, ob Cookies gesetzt wurden und in Session übernehmen
            try:
                cookie = driver.get_cookie("bm_sz")
                if cookie:
                    self.session.cookies.clear()
                    self.session.cookies.update({c['name']: c['value'] for c in driver.get_cookies()})
                    self.log.info("Browser-Cookie generiert: *{}".format(cookie.get("value")[-6:]))
                    return True
                self.log.error("Cookies können nicht erstellt werden!")
                return False
            except:
                return False

    @retry_on_failure()
    def login(self):
        """Einloggen mittels Code, um qualifizierte Impfstoffe zu erhalten.
        Dieser Schritt ist wahrscheinlich nicht zwigend notwendig, aber schadet auch nicht.

        :return: bool
        """

        path = f"rest/login?plz={self.plz}"

        res = self.session.get(self.domain + path, timeout=15)
        if res.ok:
            # Checken, welche Impfstoffe für das Alter zur Verfügung stehen
            self.qualifikationen = res.json().get("qualifikationen")
            if self.qualifikationen:
                zugewiesene_impfstoffe = " ".join(
                    [self.verfuegbare_impfstoffe.get(q, "N/A")
                     for q in self.qualifikationen])
                self.log.info("Erfolgreich mit Code eingeloggt")
                self.log.info(f"Qualifizierte Impfstoffe: {zugewiesene_impfstoffe}")
                print(" ")

                return True
            self.log.error("Keine qualifizierten Impfstoffe verfügbar!")
        else:
            self.log.error("Einloggen mit Code nicht möglich!")
        return False

    @retry_on_failure()
    def terminsuche(self):
        """Es wird nach einen verfügbaren Termin in der gewünschten PLZ gesucht.
        Ausgewählt wird der erstbeste Termin (!).
        Zurückgegeben wird das Ergebnis der Abfrage und der Status-Code.
        Bei Status-Code > 400 müssen die Cookies erneuert werden.

        Beispiel für ein Termin-Paar:

        [{
            'slotId': 'slot-56817da7-3f46-4f97-9868-30a6ddabcdef',
            'begin': 1616999901000,
            'bsnr': '005221080'
        }, {
            'slotId': 'slot-d29f5c22-384c-4928-922a-30a6ddabcdef',
            'begin': 1623999901000,
            'bsnr': '005221080'
        }]

        :return: bool, status-code
        """

        path = f"rest/suche/impfterminsuche?plz={self.plz}"

        res = self.session.get(self.domain + path, timeout=15)
        if res.ok:
            res_json = res.json()
            terminpaare = res_json.get("termine")
            if terminpaare:
                # Auswahl des erstbesten Terminpaares
                self.terminpaar = terminpaare[0]
                self.log.success("Terminpaar gefunden!")

                for num, termin in enumerate(self.terminpaar, 1):
                    ts = datetime.fromtimestamp(termin["begin"] / 1000).strftime('%d.%m.%Y um %H:%M Uhr')
                    self.log.success(f"{num}. Termin: {ts}")
                return True, 200
            self.log.info("Keine Termine verfügbar")
        else:
            self.log.error("Terminpaare können nicht geladen werden")
        return False, res.status_code

    @retry_on_failure()
    def termin_buchen(self):
        """Termin wird gebucht für die Kontaktdaten, die beim Starten des
        Programms eingetragen oder aus der JSON-Datei importiert wurden.

        :return: bool
        """

        path = "rest/buchung"

        # Daten für Impftermin sammeln
        data = {
            "plz": self.plz,
            "slots": [self.terminpaar[0].get("slotId"), self.terminpaar[1].get("slotId")],
            "qualifikationen": self.qualifikationen,
            "contact": self.kontakt
        }

        res = self.session.post(self.domain + path, json=data, timeout=15)
        if res.status_code == 201:
            self.log.success("Termin erfolgreich gebucht!")
            return True
        self.log.error("Termin konnte nicht gebucht werden")
        return False

    @staticmethod
    def run(code: str, plz: str, kontakt: json, check_delay: int = 60):
        """Workflow für die Terminbuchung.

        :param code: 14-stelliger Impf-Code
        :param plz: PLZ des Impfzentrums
        :param kontakt: Kontaktdaten der zu impfenden Person als JSON
        :param check_delay: Zeit zwischen Iterationen der Terminsuche
        :return:
        """

        its = ImpfterminService(code, plz, kontakt)
        its.cookies_erneuern()
        while not its.login():
            its.cookies_erneuern()
            time.sleep(3)

        termin_gefunden = False
        while not termin_gefunden:
            termin_gefunden, status_code = its.terminsuche()
            if status_code >= 400:
                its.cookies_erneuern()
            elif not termin_gefunden:
                time.sleep(check_delay)

        its.termin_buchen()

def generate_kontaktdaten(filepath):
    print(
        "Bitte trage zunächst deinen Impfcode und deine Kontaktdaten ein.\n"
        f"Die Daten werden anschließend lokal in der Datei {filepath} abgelegt.\n"
        "Du musst sie zukünftig nicht mehr eintragen.\n")
    code = input("Code: ")
    plz = input("PLZ des Impfzentrums: ")

    anrede = input("Anrede (Frau/Herr/...): ")
    vorname = input("Vorname: ")
    nachname = input("Nachname: ")
    strasse = input("Strasse: ")
    hausnummer = input("Hausnummer: ")
    wohnort_plz = input("PLZ des Wohnorts: ")
    wohnort = input("Wohnort: ")
    telefonnummer = input("Telefonnummer: ")
    mail = input("Mail: ")

    kontakt = {
        "anrede": anrede,
        "vorname": vorname,
        "nachname": nachname,
        "strasse": strasse,
        "hausnummer": hausnummer,
        "plz": wohnort_plz,
        "ort": wohnort,
        "phone": "+49" + str(telefonnummer),
        "notificationChannel": "email",
        "notificationReceiver": mail,
    }

    kontaktdaten = {
        "code": code,
        "plz": plz,
        "kontakt": kontakt
    }

    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(kontaktdaten, f, ensure_ascii=False, indent=4)

def main():
    """ entry point """
    print("vaccipy 1.0\n")

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-d", "--delay",
        type=float,
        default=30,
        help="the delay between requests")
    parser.add_argument(
        "-g", "--generate",
        default=False,
        action="store_true",
        help="generate a file with the kontaktdaten")
    args = parser.parse_args()

    if args.generate:
        generate_kontaktdaten('kontaktdaten.json')
        return 0

    if not os.path.isfile('kontaktdaten.json'):
        print(
            f"Datei 'kontaktdaten.json' ist nicht vorhanden bitte nutzen sie "
            "die --generate option, um die Konfiguration zu erzeugen")
    with open("kontaktdaten.json") as f:
        kontaktdaten = json.load(f)

    try:
        code = kontaktdaten["code"]
        plz = kontaktdaten["plz"]
        kontakt = kontaktdaten["kontakt"]
        print(f"Kontaktdaten wurden geladen für: {kontakt['vorname']} {kontakt['nachname']}\n")
        ImpfterminService.run(code=code, plz=plz, kontakt=kontakt, check_delay=args.delay)
        return 0

    except KeyError:
        print("Kontaktdaten konnten nicht aus 'kontaktdaten.json' geladen werden.\n"
              "Bitte überprüfe, ob sie im korrekten JSON-Format sind oder gebe "
              "deine Daten mit Hilfe der Option '--generate' beim Programmstart "
              "erneut ein.")
    return 1

if __name__ == "__main__":
    sys.exit(main())
