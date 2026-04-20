import { Component, Vue } from 'vue-property-decorator'

// Mixin that reads the stealth_autoloader Klipper object from the Vuex store.
// Mainsail automatically populates $store.state.printer.stealth_autoloader
// from Klipper's get_status() return value via the Moonraker WebSocket.

@Component
export default class StealthAutoloaderMixin extends Vue {
    get saExists(): boolean {
        return this.$store.state.printer?.stealth_autoloader !== undefined
    }

    get sa(): Record<string, any> {
        return this.$store.state.printer?.stealth_autoloader ?? {}
    }

    get saNumPaths(): number {
        return this.sa.num_paths ?? 0
    }

    get saCurrentPath(): number {
        return this.sa.current_path ?? -1
    }

    get saServoEngaged(): boolean {
        return this.sa.servo_engaged ?? false
    }

    get saPathStates(): string[] {
        return this.sa.path_states ?? []
    }

    get saEntryFilament(): boolean[] {
        return this.sa.entry_filament ?? []
    }

    get saToolheadFilament(): boolean[] {
        return this.sa.toolhead_filament ?? []
    }

    get saExtruderFilament(): boolean[] {
        return this.sa.extruder_filament ?? []
    }

    get saPathMaterials(): string[] {
        return this.sa.path_materials ?? []
    }

    get saPathBrands(): string[] {
        return this.sa.path_brands ?? []
    }

    get saPathColorNames(): string[] {
        return this.sa.path_color_names ?? []
    }

    get saPathColorHexes(): string[] {
        return this.sa.path_color_hexes ?? []
    }

    // Returns an array of per-path objects for easy v-for iteration
    get saPaths(): Array<{
        index: number
        state: string
        active: boolean
        entry: boolean
        toolhead: boolean
        extruder: boolean
        material: string
        brand: string
        colorName: string
        colorHex: string
    }> {
        const n = this.saNumPaths
        const result = []
        for (let i = 0; i < n; i++) {
            result.push({
                index:     i,
                state:     this.saPathStates[i]        ?? 'unknown',
                active:    i === this.saCurrentPath,
                entry:     this.saEntryFilament[i]     ?? false,
                toolhead:  this.saToolheadFilament[i]  ?? false,
                extruder:  this.saExtruderFilament[i]  ?? false,
                material:  this.saPathMaterials[i]     ?? '',
                brand:     this.saPathBrands[i]        ?? '',
                colorName: this.saPathColorNames[i]    ?? '',
                colorHex:  this.saPathColorHexes[i]    ?? '',
            })
        }
        return result
    }
}
