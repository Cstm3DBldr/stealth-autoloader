<template>
  <panel-card :title="$t('AutoloaderPanel.title', 'Autoloader')">
    <template #buttons>
      <v-btn icon small @click="sendGcode('SA_HOME')" title="Home Selector">
        <v-icon>mdi-home</v-icon>
      </v-btn>
      <v-btn icon small @click="fetchStatus" title="Refresh">
        <v-icon>mdi-refresh</v-icon>
      </v-btn>
    </template>

    <!-- Status table -->
    <v-simple-table dense class="sa-table" v-if="status">
      <thead>
        <tr>
          <th>#</th>
          <th>STATE</th>
          <th>EN</th>
          <th>TH</th>
          <th>EX</th>
          <th>MATERIAL</th>
          <th>COLOR / BRAND</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="i in numPaths" :key="i - 1">
          <td class="font-weight-bold">T{{ i - 1 }}</td>
          <td>
            <span :class="stateClass(pathStates[i-1])">
              {{ stateLabel(pathStates[i-1]) }}
            </span>
          </td>
          <td><span :class="dotClass(entryFilament[i-1])">{{ entryFilament[i-1] ? '●' : '○' }}</span></td>
          <td><span :class="dotClass(toolheadFilament[i-1])">{{ toolheadFilament[i-1] ? '●' : '○' }}</span></td>
          <td><span :class="dotClass(extruderFilament[i-1])">{{ extruderFilament[i-1] ? '●' : '○' }}</span></td>
          <td>{{ pathMaterials[i-1] || '---' }}</td>
          <td>
            <span class="d-flex align-center">
              <span
                v-if="pathColorHexes[i-1]"
                class="sa-swatch mr-1"
                :style="{ background: pathColorHexes[i-1] }"
              ></span>
              {{ pathColorNames[i-1] || '---' }}
              <span class="grey--text ml-1" v-if="pathBrands[i-1]">· {{ pathBrands[i-1] }}</span>
            </span>
          </td>
          <td>
            <v-btn x-small outlined @click="openLoadDialog(i - 1)">LOAD</v-btn>
          </td>
        </tr>
      </tbody>
    </v-simple-table>

    <!-- Footer bar -->
    <div class="sa-footer mt-2" v-if="status">
      <span class="caption grey--text">
        Selector: {{ selectorStr }}
        &nbsp;|&nbsp;
        Drive: {{ status.servo_engaged ? 'ENGAGED' : 'neutral' }}
      </span>
    </div>

    <!-- Load wizard dialog -->
    <v-dialog v-model="dialog" max-width="600">
      <v-card>
        <v-card-title>Load T{{ wizard.path }}</v-card-title>
        <v-card-text>
          <!-- Step indicator -->
          <v-stepper v-model="step" alt-labels>
            <v-stepper-header>
              <v-stepper-step step="1">Brand</v-stepper-step>
              <v-divider/>
              <v-stepper-step step="2">Material</v-stepper-step>
              <v-divider/>
              <v-stepper-step step="3">Line</v-stepper-step>
              <v-divider/>
              <v-stepper-step step="4">Color</v-stepper-step>
            </v-stepper-header>
            <v-stepper-items>

              <!-- Step 1: Brand -->
              <v-stepper-content step="1">
                <div class="d-flex flex-wrap gap-2">
                  <v-btn
                    v-for="b in brands" :key="b.filepath"
                    outlined small class="ma-1"
                    @click="selectBrand(b)">
                    {{ b.display_name }}
                  </v-btn>
                </div>
              </v-stepper-content>

              <!-- Step 2: Material -->
              <v-stepper-content step="2">
                <div class="d-flex flex-wrap gap-2">
                  <v-btn
                    v-for="m in materials" :key="m"
                    outlined small class="ma-1"
                    @click="selectMaterial(m)">
                    {{ m }}
                  </v-btn>
                </div>
              </v-stepper-content>

              <!-- Step 3: Product line -->
              <v-stepper-content step="3">
                <v-list dense>
                  <v-list-item
                    v-for="pl in filteredLines" :key="pl.line_id"
                    @click="selectLine(pl)">
                    <v-list-item-content>
                      <v-list-item-title>{{ pl.display_name }}</v-list-item-title>
                      <v-list-item-subtitle>
                        Load: {{ pl.load_temp }}°C · Bed: {{ pl.bed_temp }}°C
                        <span v-if="pl.description"> · {{ pl.description.slice(0, 60) }}</span>
                      </v-list-item-subtitle>
                    </v-list-item-content>
                  </v-list-item>
                </v-list>
              </v-stepper-content>

              <!-- Step 4: Color -->
              <v-stepper-content step="4">
                <div class="sa-color-scroll d-flex flex-nowrap overflow-x-auto pb-2">
                  <div
                    v-for="c in colors" :key="c.id"
                    class="sa-color-chip ma-1 text-center"
                    :class="{ 'sa-color-chip--selected': wizard.colorHex === c.hex }"
                    @click="selectColor(c)">
                    <div class="sa-swatch-lg mx-auto" :style="{ background: c.hex }"></div>
                    <div class="caption" style="max-width:64px;word-break:break-word">{{ c.name }}</div>
                  </div>
                </div>
                <!-- Selected color info -->
                <div v-if="wizard.colorHex" class="d-flex align-center mt-2">
                  <div class="sa-swatch-lg mr-2" :style="{ background: wizard.colorHex }"></div>
                  <div>
                    <div class="font-weight-bold">{{ wizard.colorName }}</div>
                    <div class="caption grey--text">
                      {{ wizard.colorHex }} · {{ wizard.lineName }}
                      · Load: {{ wizard.loadTemp }}°C
                    </div>
                  </div>
                </div>
              </v-stepper-content>

            </v-stepper-items>
          </v-stepper>
        </v-card-text>
        <v-card-actions>
          <v-btn text @click="dialog = false">Cancel</v-btn>
          <v-spacer/>
          <v-btn text v-if="step > 1" @click="step--">Back</v-btn>
          <v-btn
            color="primary"
            v-if="step === 4 && wizard.colorHex"
            @click="confirmLoad">
            LOAD
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

  </panel-card>
</template>

<script>
export default {
  name: 'AutoloaderPanel',

  data() {
    return {
      status: null,
      brands: [],
      productLines: [],
      materials: [],
      filteredLines: [],
      colors: [],
      dialog: false,
      step: 1,
      wizard: {
        path: 0,
        brandPath: '',
        brandName: '',
        material: '',
        lineId: '',
        lineName: '',
        colorId: '',
        colorName: '',
        colorHex: '',
        loadTemp: 200,
        unloadTemp: 185,
        purgeSpeed: 5,
        purgeLength: 30,
      },
      _pollTimer: null,
    }
  },

  computed: {
    numPaths()         { return this.status?.num_paths         ?? 0 },
    pathStates()       { return this.status?.path_states       ?? [] },
    entryFilament()    { return this.status?.entry_filament    ?? [] },
    toolheadFilament() { return this.status?.toolhead_filament ?? [] },
    extruderFilament() { return this.status?.extruder_filament ?? [] },
    pathMaterials()    { return this.status?.path_materials    ?? [] },
    pathBrands()       { return this.status?.path_brands       ?? [] },
    pathColorNames()   { return this.status?.path_color_names  ?? [] },
    pathColorHexes()   { return this.status?.path_color_hexes  ?? [] },
    selectorStr() {
      const cp = this.status?.current_path ?? -1
      return cp >= 0 ? `Path ${cp}` : 'unhomed'
    },
  },

  mounted() {
    this.fetchStatus()
    this.fetchBrands()
    this._pollTimer = setInterval(this.fetchStatus, 2000)
  },

  beforeDestroy() {
    if (this._pollTimer) clearInterval(this._pollTimer)
  },

  methods: {
    apiBase() {
      return `${window.location.protocol}//${window.location.hostname}:7125`
    },

    async fetchStatus() {
      try {
        const r = await fetch(`${this.apiBase()}/machine/autoloader/status`)
        if (r.ok) this.status = await r.json()
      } catch (e) { /* printer offline */ }
    },

    async fetchBrands() {
      try {
        const r = await fetch(`${this.apiBase()}/machine/autoloader/brands`)
        if (r.ok) {
          const d = await r.json()
          this.brands = d.brands ?? []
        }
      } catch (e) { /* ignore */ }
    },

    async openLoadDialog(path) {
      this.wizard.path = path
      this.step = 1
      this.dialog = true
    },

    async selectBrand(b) {
      this.wizard.brandPath = b.filepath
      this.wizard.brandName = b.display_name
      // Fetch all product lines for this brand
      const r = await fetch(
        `${this.apiBase()}/machine/autoloader/filaments?brand=${encodeURIComponent(b.filepath)}`)
      if (r.ok) {
        const d = await r.json()
        this.productLines = d.product_lines ?? []
        const seen = new Set()
        this.materials = this.productLines
          .map(pl => pl.material).filter(m => { if (seen.has(m)) return false; seen.add(m); return true })
      }
      this.step = 2
    },

    selectMaterial(mat) {
      this.wizard.material = mat
      this.filteredLines = this.productLines.filter(pl => pl.material === mat)
      this.step = 3
    },

    selectLine(pl) {
      this.wizard.lineId     = pl.line_id
      this.wizard.lineName   = pl.display_name
      this.wizard.loadTemp   = pl.load_temp
      this.wizard.unloadTemp = pl.unload_temp
      this.wizard.purgeSpeed = pl.purge_speed
      this.wizard.purgeLength = pl.purge_length
      this.colors = pl.colors ?? []
      this.step = 4
    },

    selectColor(c) {
      this.wizard.colorId   = c.id
      this.wizard.colorName = c.name
      this.wizard.colorHex  = c.hex
    },

    async confirmLoad() {
      const wz = this.wizard
      // 1. Set material profile
      await fetch(`${this.apiBase()}/machine/autoloader/set_material`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          tool:         wz.path,
          material:     wz.material,
          brand:        wz.brandName,
          line:         wz.lineId,
          color_name:   wz.colorName,
          color_hex:    wz.colorHex,
          load_temp:    wz.loadTemp,
          unload_temp:  wz.unloadTemp,
          purge_speed:  wz.purgeSpeed,
          purge_length: wz.purgeLength,
        }),
      })
      // 2. Start load
      await fetch(`${this.apiBase()}/machine/autoloader/load`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tool: wz.path }),
      })
      this.dialog = false
    },

    async sendGcode(cmd) {
      await fetch(`${this.apiBase()}/printer/gcode/script`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ script: cmd }),
      })
    },

    stateLabel(s) {
      return { loaded: '● LOADED', empty: '○ EMPTY', partial: '≈ PARTIAL', unknown: '? UNKNOWN' }[s] ?? '? UNKNOWN'
    },
    stateClass(s) {
      return { loaded: 'green--text', empty: 'grey--text', partial: 'orange--text', unknown: 'yellow--text' }[s] ?? ''
    },
    dotClass(v) { return v ? 'green--text' : 'grey--text' },
  },
}
</script>

<style scoped>
.sa-table th { font-size: 11px; color: #aaa; }
.sa-swatch {
  display: inline-block;
  width: 14px; height: 14px;
  border-radius: 50%;
  vertical-align: middle;
}
.sa-swatch-lg {
  width: 36px; height: 36px;
  border-radius: 50%;
}
.sa-color-chip { cursor: pointer; min-width: 68px; }
.sa-color-chip--selected .sa-swatch-lg { outline: 3px solid #42A5F5; }
.sa-footer { border-top: 1px solid #333; padding-top: 4px; }
</style>
