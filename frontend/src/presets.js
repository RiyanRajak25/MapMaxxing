// Sentinel-2 L2A opens 2017-03-28, so the dry season beginning November 2017 is
// the first one the archive covers in full.
export const EARLIEST_YEAR = 2017

// The most recent year whose Nov-Feb dry season has closed. Mirrors
// imagery.latest_complete_dry_season_year on the backend; a year still in
// progress would composite fewer scenes than the one it is compared against.
export function latestCompleteYear(today = new Date()) {
  // getMonth() is zero-based, so > 1 means March or later.
  return today.getMonth() > 1 ? today.getFullYear() - 1 : today.getFullYear() - 2
}

export function yearOptions() {
  const latest = latestCompleteYear()
  return Array.from({ length: latest - EARLIEST_YEAR + 1 }, (_, i) => EARLIEST_YEAR + i)
}

// Periods are chosen as a year and expand server-side to 1 Nov - end of Feb.
// This is deliberate: free date ranges made it trivial to compare November
// against April and read seasonal phenology as urban growth.
export function drySeasonLabel(year) {
  return `Nov ${year}-Feb ${year + 1}`
}

// Prototype scope is the Bhoj Wetland only. The wider Bhopal extent was useful
// for reproducing the paper's headline figure but is not what this tool is for.
export const BHOJ_WETLAND = {
  id: 'bhoj',
  name: 'Bhoj Wetland, Bhopal',
  note: 'Upper and Lower Lakes plus the urban belt around them: the Ramsar site and its margin.',
  aoi: {
    type: 'Polygon',
    coordinates: [
      [
        [77.22, 23.18],
        [77.48, 23.18],
        [77.48, 23.32],
        [77.22, 23.32],
        [77.22, 23.18],
      ],
    ],
  },
  bounds: [
    [23.18, 77.22],
    [23.32, 77.48],
  ],
  // The paper's study period: "the built-up area delineated during 2017 and
  // 2024", reported as ~31.8% growth. A year here means the dry season
  // beginning in it, so this is Nov 2017-Feb 2018 against Nov 2024-Feb 2025.
  year1: 2017,
  year2: 2024,
}

export const PRESETS = [BHOJ_WETLAND]
export const DEFAULT_PRESET = BHOJ_WETLAND
