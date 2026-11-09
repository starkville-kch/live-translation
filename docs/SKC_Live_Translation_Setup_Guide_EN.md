# Getting Started with SKC Live Translation

**Installation and first-use guide**

v3.4.4 · November 9, 2026

SKC Live Translation is a Windows application that provides live captions and spoken translation during worship. You can use it at your church by entering your church name, logo, and local address during setup.

You do not need to install Python or Conda. Open `SKC_setup.exe` and follow the steps below. You do not need to edit configuration files by hand.

For example, Jackson Korean Church can use these settings:

```text
Church name: Jackson Korean Church
Short name: JKC
Local address: http://jkc.local:8080
```

Setup is needed only once. For later services, open `SKC_translation.exe`.

## 1. Extract the ZIP and open setup

Extract the downloaded ZIP into a new folder. Open that folder before running the application. Please do not run the EXE from inside the ZIP.

Open this file:

```text
SKC_setup.exe
```

When the setup window opens, start with your church information. It is fine if you do not have a Google API key yet. You will prepare it in the next steps.

> If you are replacing an older version, close it first and keep its folder as a backup. Copy its `.env`, `config.yaml`, and `branding/` into the new folder to keep your existing settings.

## 2. Enter your church name and local address

Use **1. CHURCH & LOCAL NETWORK** on the left side of the setup window.

For Jackson Korean Church, enter:

```text
Church Name: Jackson Korean Church
Short Name: JKC
Local URL: jkc
```

Enter your church's full name in **Church Name**. Use **Short Name** for the shorter name shown on phones.

In **Local URL**, enter only a short name such as `jkc`. The application adds the `.local:8080` shown beside the field. With this example, the full address is `http://jkc.local:8080`.

If you have a church logo, select it with **Choose Logo...**. The church name and logo will appear on the translation screen.

**Operator Pass** is an optional password for the operator console. Leave it empty if you want to use the console without logging in. If you set a password, keep it with the people who operate the system.

## 3. Open Google AI Studio

The application uses Google Gemini for translation. Before first use, you need a Google API project with billing enabled.

On the right side of the setup window, click:

```text
Open Google AI Studio
```

When AI Studio opens in your browser, sign in with a Google account that your church can continue to manage.

If you already have a paid Gemini project and its API key, you can skip steps 4 and 5 and enter that key in step 6.

## 4. Prepare the project and billing

In AI Studio, select the project you want to use. If you do not have one, create a project with a name that is easy for your church to recognize.

For example:

```text
JKC-Live-Translation
```

Choose **Set up billing** for that project and link a billing account. Follow Google's instructions to enter your country, address, and payment details.

If you see **Prepay** or a credit purchase screen, check the amount and terms shown before adding credit. This credit pays for Gemini API usage.

Actual API usage is deducted from the credit. The remaining balance can be used for later services. A low balance can interrupt translation, so please check the Billing screen before worship.

Google may change its screens or billing terms. Follow the current screen and [Google's billing guide](https://aistudio.google.com/docs/billing).

## 5. Create and copy the API key

In AI Studio, open **API Keys** and choose **Create API key**. Select the project you connected to billing in the previous step.

Once the key is created, click **Copy**.

Please treat the API key like a password. Do not put it on your church website, in public documents, in group chats, or on attendee handouts. Attendees only need the QR code or address you will prepare later.

If you already have a working key for your paid project, you can keep using it. [Google's API key guide](https://ai.google.dev/gemini-api/docs/api-key) is also available when you need a new key.

## 6. Paste the key and check the connection

Return to `SKC_setup.exe`. Paste your key into the API Key field under **2. GOOGLE GEMINI API**. The key is hidden on screen by default.

Then click:

```text
Test Connection & Model Availability
```

The application contacts Google to check key authentication and model availability. A green **Gemini verified** label means the check passed.

A yellow **API key set** label means a key has been entered. If you have not tested it yet, click the button above.

If an error appears, check that you copied the whole key, the PC is online, and billing is ready for the project you selected.

## 7. Set up public HTTPS only if you need it

If attendees will connect to the church Wi-Fi, you can skip **3. PUBLIC HTTPS (Advanced)**. Local Wi-Fi is a simple way to begin.

Expand this section if attendees need access over cellular data or from outside the church. You will need a Cloudflare account, a domain, and a configured tunnel.

Enter the public URL and tunnel token. If needed, use **Download cloudflared.exe** to download the connection program. See [Cloudflare's setup guide](https://developers.cloudflare.com/tunnel/get-started/) for the preparation steps.

You may briefly see `Checking...` after expanding the section. The application is checking the Windows service, and you can continue entering other information. Scroll if the lower controls are outside the window.

Public HTTPS is for attendees. Open the operator console on the PC or through the church Wi-Fi.

## 8. Save your settings

Once you have entered the settings and checked the connection, click **Save & Finish** at the bottom of the window.

Your church information and API key are saved. The application also creates these files in the same folder:

```text
qr_local.png
qr_local_print.html
```

`qr_local.png` is a QR image for a TV screen or bulletin. `qr_local_print.html` is a printable page with your church name, QR code, and connection instructions.

Your initial setup is complete. Next, prepare the QR handout and try connecting with a phone.

## 9. Print or save the QR handout

Double-click `qr_local_print.html`. On the page that opens, click **Print** or press `Ctrl+P`.

Choose A4 or Letter paper and check that the handout fits on one page. You can print it or choose **Save as PDF** as the destination.

If you only need the image, use `qr_local.png`.

While the application is running, you can also save or print the QR code from **Attendee Access QR** on the operator console:

- Right-click the QR image and choose **Save image as**.
- Click **Print Local Wi-Fi Poster**, then **Print Poster** in the window that opens.
- If public access is configured, select **Local Wi-Fi**. The print button may be labeled **View Both QR Codes (Printable)**.

The QR code contains the address of the translation screen. Attendees should join the church Wi-Fi before scanning it. Scanning the QR code does not connect a phone to Wi-Fi automatically.

## 10. Try a phone before worship

On the PC, open:

```text
SKC_translation.exe
```

When the operator console opens, select the microphone you will use and click **Start Translation**. Say a sentence or two in the languages used during your service and check the source text and translation.

Connect a phone to the church Wi-Fi and scan the QR code. Check that captions appear and translated audio plays. Please use earphones for translated audio.

> The PC and translation application must be running for a phone to reach the QR address. Even when attendees use local Wi-Fi, the PC needs an Internet connection for Gemini translation.

If the address does not open on a phone, first check that it is on the same Wi-Fi. You can also try the local IP address shown on the operator console. Some guest networks block connections between devices; your church's network administrator can help check this.

## 11. Use it during weekly worship

You do not need to run setup each week.

1. Check the PC and phone network connections.
2. Open `SKC_translation.exe`.
3. Select the microphone and click **Start Translation**.
4. Do a short caption and audio check.
5. Show the QR code so attendees can connect.
6. After worship, click **Stop Service**, then exit the application.

Use Pause and Resume for a short break in translation. Translation context is preserved, and input recording continues. To start with fresh context, click **Stop Service**, then **Start Translation**.

Automatic translation can mishear speech or transcribe it in another language. Please check both the source text and translation before the service.

## 12. Find recordings or update settings later

Input audio is saved as MP3 in this folder. Recording continues while translation is paused.

```text
logs/recordings/current_week/
```

When a new recording starts, generated recordings older than 21 days are cleaned up. Copy audio you want to keep into another folder. A recording warning can mean that no MP3 is being saved, so please check it before worship.

Transcripts and session summaries are in `logs/sessions/`. To align them with video, first check when recording began. Transcript times include the delay before translated output arrives.

If startup is slow or an error occurs, `logs/startup.log` and `logs/ops.log` can help identify the problem. These logs are created during normal use; a separate diagnostic run is not needed.

Run `SKC_setup.exe` again when you need to change the church name, logo, local address, or API key. Use **Replace Key...** to change an existing key. If you change the local address, please replace your QR handouts too.

If an update causes trouble, exit the new application and open `SKC_translation.exe` from the older folder you kept.

You can find the latest version and update information on the project's GitHub page:

[github.com/starkville-kch/live-translation](https://github.com/starkville-kch/live-translation)
