# Document review: approve what the chat may quote

Run from the project folder. Approve all good ones in one go:

```bash
uv run python scripts/review_corpus.py approve <key>
```

Mark `x` in the first column for anything to REJECT, then tell Claude.
**Check** = the website is not a .gov.in / .nic.in domain, so decide whether it counts as official.

| Reject? | Key | Layer | Chunks | Website | Check |
|---|---|---|---|---|---|
|  | `air-delhi-112` | delhi | 1 | [www.newsonair.gov.in](https://www.newsonair.gov.in/cm-rekha-gupta-says-important-initiative-is-being-undertaken-to-make-emergency-services-in-faster-simpler-technology-driven/) |  |
|  | `brpl-contact` | delhi | 1 | [www.bsesdelhi.com](https://www.bsesdelhi.com/web/brpl/contact-us) | ⚠️ check |
|  | `brpl-streetlight` | delhi | 1 | [www.bsesdelhi.com](https://www.bsesdelhi.com/web/brpl/street-light-complaint) | ⚠️ check |
|  | `bypl-contact` | delhi | 1 | [www.bsesdelhi.com](https://www.bsesdelhi.com/web/bypl/contact-us) | ⚠️ check |
|  | `cpwd-sewa` | delhi | 1 | [cpwdsewa.gov.in](https://cpwdsewa.gov.in/Public_Portal/main.aspx) |  |
|  | `derc-street-lighting` | delhi | 14 | [www.derc.gov.in](https://www.derc.gov.in/sites/default/files/StreetLighting.pdf) |  |
|  | `dfs-node-1103` | delhi | 2 | [dfs.delhi.gov.in](https://dfs.delhi.gov.in/hi/node/1103) |  |
|  | `dgde-delhi-cantt` | delhi | 2 | [dgde.gov.in](https://dgde.gov.in/?p=9117) |  |
|  | `djb-grievance` | delhi | 7 | [delhijalboard.delhi.gov.in](https://delhijalboard.delhi.gov.in/jalboard/grievance-redressal-mechanism) |  |
|  | `dpcc-home` | delhi | 1 | [dpcc.delhi.gov.in](https://dpcc.delhi.gov.in/) |  |
|  | `dtc-feedback` | delhi | 1 | [dtc.delhi.gov.in](https://dtc.delhi.gov.in/dtc/feedback) |  |
|  | `dtp-node-1081` | delhi | 1 | [traffic.delhipolice.gov.in](https://traffic.delhipolice.gov.in/en/node/1081) |  |
|  | `dtp-raise-complaint` | delhi | 1 | [traffic.delhipolice.gov.in](https://traffic.delhipolice.gov.in/en/raise-complaint) |  |
|  | `forest-home` | delhi | 1 | [forest.delhi.gov.in](https://forest.delhi.gov.in/) |  |
|  | `ifc-flood-order-2025` | delhi | 273 | [ifc.delhi.gov.in](https://ifc.delhi.gov.in/sites/default/files/ifc/universal/fco_2025.pdf) |  |
|  | `ifc-home` | delhi | 3 | [ifc.delhi.gov.in](https://ifc.delhi.gov.in/) |  |
|  | `mcd-portal` | delhi | 1 | [mcdonline.nic.in](https://mcdonline.nic.in/portal) |  |
|  | `ndmc-cgrf` | delhi | 2 | [online.ndmc.gov.in](https://online.ndmc.gov.in/ecgrf/contents/grievance-redressal-procedure/three-tier-grievance-redressal-structure) |  |
|  | `ndmc-grievance` | delhi | 1 | [ndmc.gov.in](https://ndmc.gov.in/departments/commercial_grievance.aspx) |  |
|  | `ngt-dda-oa152-2023` | delhi | 28 | [www.greentribunal.gov.in](https://www.greentribunal.gov.in/sites/default/files/news_updates/Compliance%20Report%20by%20DDA%20in%20OA%20No.%20152%20of%202023%20(RAJARAM%20C.%20IYER%20VS%20GOVT.%20OF%20N.C.T.%20OF%20DELHI).pdf) |  |
|  | `ngt-mcd-ea05-2024` | delhi | 11 | [www.greentribunal.gov.in](https://www.greentribunal.gov.in/sites/default/files/news_updates/Report%20by%20MCD%20in%20EA%20No.%2005%20of%202024%20IN%20OA%20No.%2006%20of%202012%20(Nizamuddin%20West%20Association%20vs%20UOI%20%26ors.%20).pdf) |  |
|  | `ngt-pwd-oa559-2022` | delhi | 8 | [www.greentribunal.gov.in](https://www.greentribunal.gov.in/sites/default/files/news_updates/REPLY%20BY%20R-8%20PWD%20GNCTD%20IN%20OA%20NO.%20559%20of%202022%20SANJEEV%20JAIN%20VS%20MUNICIPAL%20CORPORATION%20OF%20DELHI%20%26%20ORS.pdf) |  |
|  | `ngt-sdmc-oa06-2012` | delhi | 10 | [www.greentribunal.gov.in](https://www.greentribunal.gov.in/sites/default/files/news_updates/Affidavit%20by%20SDMC%20in%20OA%20No.%2006%20of%202012.pdf) |  |
|  | `pgms-delhi` | delhi | 1 | [pgms.delhi.gov.in](https://pgms.delhi.gov.in/) |  |
|  | `pwd-grievance` | delhi | 1 | [www.pwddelhi.gov.in](https://www.pwddelhi.gov.in/grievance-redress) |  |
|  | `pwd-roads-inventory` | delhi | 1 | [www.pwddelhi.gov.in](https://www.pwddelhi.gov.in/maintenance-inventory/roads-with-pwd) |  |
|  | `rs-q-3923-dtc` | delhi | 1 | [rsdebate.nic.in](https://rsdebate.nic.in/bitstream/123456789/634560/2/PQ_232_14082014_U3923_p185_p186.pdf) |  |
|  | `tpddl-charter-2022` | delhi | 22 | [tatapower-ddl.com](https://tatapower-ddl.com/Editor_UploadedDocuments/Content/Consumer_Charter_Nov_9%2C_2022.pdf) | ⚠️ check |
|  | `air-cpgrams-guidelines` | national | 1 | [www.newsonair.gov.in](https://www.newsonair.gov.in/centre-issues-comprehensive-guidelines-for-public-grievance-redressal) |  |
|  | `air-nhai-1033` | national | 1 | [newsonair.gov.in](https://newsonair.gov.in/nhai-to-upgrade-1033-national-highway-helpline-with-advanced-technology/) |  |
|  | `cpgrams-guidelines-2024` | national | 24 | [pgportal.gov.in](https://pgportal.gov.in/Home/Preview/Q29tcHJlaGVuc2l2ZUd1aWRlbGluZXNGb3JIYW5kbGluZ1RoZVB1YmxpY0dyaWV2YW5jZXMucGRm) |  |
|  | `cpgrams-portal` | national | 1 | [pgportal.gov.in](https://pgportal.gov.in/) |  |
|  | `rti-rules-2012` | national | 1 | [ncte.gov.in](https://ncte.gov.in/website/pdf/RTI/RTI_Rules_2012_31_07_2012.pdf) |  |
|  | `hp-psg-2011` | state | 43 | [eudyan.hp.gov.in](https://eudyan.hp.gov.in/cms/media/31udn3ad/psg-2011.pdf) |  |
|  | `maharashtra-rts-2015` | state | 1 | [divcomkonkan.gov.in](https://divcomkonkan.gov.in/en/rts/) |  |
|  | `portal-an` | state | 1 | [db.and.nic.in](https://db.and.nic.in/lghelpdesk/) |  |
|  | `portal-ap` | state | 1 | [pgrs.ap.gov.in](https://pgrs.ap.gov.in/) |  |
|  | `portal-ar` | state | 1 | [cmejansunwai.arunachal.gov.in](https://cmejansunwai.arunachal.gov.in/) |  |
|  | `portal-as` | state | 38 | [sewasetu.assam.gov.in](https://sewasetu.assam.gov.in/grm-v2) |  |
|  | `portal-br` | state | 1 | [lokshikayat.bihar.gov.in](https://lokshikayat.bihar.gov.in/) |  |
|  | `portal-dh` | state | 1 | [ddd.gov.in](https://ddd.gov.in/grievances-complaints/) |  |
|  | `portal-ga` | state | 1 | [dpg.goa.gov.in](https://dpg.goa.gov.in/) |  |
|  | `portal-gj` | state | 1 | [swagat.gujarat.gov.in](https://swagat.gujarat.gov.in/) |  |
|  | `portal-hp` | state | 1 | [cmsankalp.hp.gov.in](https://cmsankalp.hp.gov.in/) |  |
|  | `portal-hr` | state | 1 | [cmharyanacell.nic.in](https://cmharyanacell.nic.in/cm_window_index) |  |
|  | `portal-jh` | state | 1 | [parishodhan.jharkhand.gov.in](https://parishodhan.jharkhand.gov.in/) |  |
|  | `portal-jk` | state | 1 | [samadhan.jk.gov.in](https://samadhan.jk.gov.in/) |  |
|  | `portal-ka` | state | 1 | [ipgrs.karnataka.gov.in](https://ipgrs.karnataka.gov.in/) |  |
|  | `portal-kl` | state | 1 | [cmo.kerala.gov.in](https://cmo.kerala.gov.in/) |  |
|  | `portal-mh` | state | 1 | [grievances.maharashtra.gov.in](https://grievances.maharashtra.gov.in/en) |  |
|  | `portal-od` | state | 1 | [janasunani.odisha.gov.in](https://janasunani.odisha.gov.in/) |  |
|  | `portal-py` | state | 1 | [lgredressal.py.gov.in](https://lgredressal.py.gov.in/pgrs/) |  |
|  | `portal-ts` | state | 2 | [prajavani.cgg.gov.in](https://prajavani.cgg.gov.in/) |  |
|  | `portal-uk` | state | 1 | [cmhelpline.uk.gov.in](https://cmhelpline.uk.gov.in/) |  |
|  | `portal-up` | state | 1 | [jansunwai.up.nic.in](https://jansunwai.up.nic.in/) |  |
